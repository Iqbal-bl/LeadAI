"""
BrowserController: high-level page actions (goto, click, type_text,
get_state, screenshot_with_boxes, upload_file, etc.) built on top of a
BrowserSession's Playwright page. Contains the DOM-indexing script that
numbers interactive elements for the LLM agent to reference.

The core trick in get_state(): instead of feeding raw HTML to the LLM (too
big, too noisy), a small JS script walks the DOM, finds visible interactive
elements in the viewport, and tags each with a stable data-agent-index
attribute. The LLM then refers to elements by index number instead of
guessing CSS selectors.
"""

import base64
import io
import random

from PIL import Image, ImageDraw, ImageFont

from .session import BrowserSession
from utils import safe_click

INDEX_SCRIPT = """
() => {
    const elements = [];
    const selector = 'a, button, input, textarea, select, [role="button"], ' +
                      '[role="link"], [onclick], [contenteditable="true"]';
    const nodes = document.querySelectorAll(selector);
    const MAX_ELEMENTS = 40;

    // Detects bold text within an element — a near-universal signal for
    // "unread" across messaging UIs (Instagram, Facebook, LinkedIn, Gmail
    // conversation/inbox previews are almost always bold when unread,
    // normal-weight once read). Checked on the element itself and its
    // descendants, since the bold text is often a nested span, not the
    // clickable container itself.
    function hasBoldText(el) {
        const nodes = [el, ...el.querySelectorAll('*')];
        for (const node of nodes) {
            const weight = parseInt(window.getComputedStyle(node).fontWeight, 10) || 400;
            if (weight >= 600) return true;
        }
        return false;
    }

    // Resolves an element's name via <label for="..."> or aria-labelledby
    // — a large fraction of real-world form inputs get their visible name
    // this way rather than via placeholder/aria-label on the input itself.
    function getLabelText(el) {
        if (el.labels && el.labels.length > 0) {
            return Array.from(el.labels).map((l) => l.innerText).join(' ').trim();
        }
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const parts = labelledBy.split(/\\s+/)
                .map((id) => document.getElementById(id))
                .filter(Boolean)
                .map((node) => node.innerText);
            if (parts.length > 0) return parts.join(' ').trim();
        }
        return '';
    }

    // Keep a persistent counter on window so an element that already has an
    // index keeps it across calls, instead of everything being renumbered
    // from scratch every time get_state() runs (which would let the model
    // click the wrong element after a scroll/lazy-load shifted the DOM).
    if (window.__agentIndexCounter === undefined) {
        window.__agentIndexCounter = 0;
    }

    for (const el of nodes) {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const inViewport = rect.bottom > 0 && rect.top < window.innerHeight &&
            rect.right > 0 && rect.left < window.innerWidth;
        const visible = rect.width > 0 && rect.height > 0 && inViewport &&
            style.visibility !== 'hidden' && style.display !== 'none';
        if (!visible) continue;

        // Skip anything currently covered by something else (a modal,
        // dialog backdrop, dropdown, etc.) — CSS visibility alone doesn't
        // catch this, since an element behind an overlay can still have a
        // valid size and not be display:none. Checking what's actually
        // topmost at its own center point does.
        //
        // Exception: many custom-styled checkboxes/radios keep the real
        // <input> visually hidden (opacity:0, 1px square) and render a
        // sibling <span>/<div> as the visible box, both wrapped in a
        // <label>. elementFromPoint then returns that sibling, not the
        // input — which would otherwise wrongly get skipped as "covered"
        // even though it's a perfectly real, clickable form control. If
        // the topmost element is (or sits inside) this element's own
        // <label>, treat it as topmost too.
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        const topEl = document.elementFromPoint(cx, cy);
        let isTopmost = topEl && (topEl === el || el.contains(topEl) || topEl.contains(el));
        if (!isTopmost && el.labels && el.labels.length > 0) {
            isTopmost = Array.from(el.labels).some(
                (label) => label === topEl || label.contains(topEl)
            );
        }
        if (!isTopmost) continue;

        let idx;
        if (el.hasAttribute('data-agent-index')) {
            idx = parseInt(el.getAttribute('data-agent-index'), 10);
        } else {
            idx = window.__agentIndexCounter++;
            el.setAttribute('data-agent-index', idx);
        }

        const labelText = getLabelText(el);
        const text = (el.innerText || el.value || el.placeholder ||
                       el.getAttribute('aria-placeholder') ||
                       labelText ||
                       el.getAttribute('aria-label') || '').trim().slice(0, 50);

        const isDisabled = el.disabled === true ||
            el.getAttribute('aria-disabled') === 'true';
        const checkedAttr = el.getAttribute('aria-checked');
        const isChecked = el.checked === true || checkedAttr === 'true'
            ? true : (el.checked === false || checkedAttr === 'false' ? false : null);

        elements.push({
            index: idx,
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            role: el.getAttribute('role') || '',
            text: text,
            href: el.getAttribute('href') || '',
            disabled: isDisabled,
            checked: isChecked,
            likelyUnread: hasBoldText(el),
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
        });
        if (elements.length >= MAX_ELEMENTS) break;
    }
    // Sort in visual reading order (top-to-bottom, left-to-right) rather
    // than DOM-traversal order — easier for the model to reason about
    // spatially. Indices themselves (the identifiers) are unaffected.
    elements.sort((a, b) => a.y - b.y || a.x - b.x);
    return elements;
}
"""


DRAW_DEBUG_OVERLAY_SCRIPT = """
(elements) => {
    const old = document.getElementById('__agent_debug_overlay__');
    if (old) old.remove();

    const overlay = document.createElement('div');
    overlay.id = '__agent_debug_overlay__';
    overlay.style.position = 'fixed';
    overlay.style.top = '0';
    overlay.style.left = '0';
    overlay.style.width = '100%';
    overlay.style.height = '100%';
    overlay.style.pointerEvents = 'none';  // never blocks real clicks
    overlay.style.zIndex = '2147483647';   // max z-index, always on top visually
    document.body.appendChild(overlay);

    const palette = ['#FF3B30', '#34C759', '#007AFF', '#FF9500', '#AF52DE', '#00B8D9'];

    for (const el of elements) {
        const color = palette[el.index % palette.length];

        const box = document.createElement('div');
        box.style.position = 'fixed';
        box.style.left = el.x + 'px';
        box.style.top = el.y + 'px';
        box.style.width = el.width + 'px';
        box.style.height = el.height + 'px';
        box.style.border = `2px solid ${color}`;
        box.style.boxSizing = 'border-box';
        box.style.pointerEvents = 'none';
        overlay.appendChild(box);

        const label = document.createElement('div');
        label.textContent = el.index;
        label.style.position = 'fixed';
        label.style.left = el.x + 'px';
        label.style.top = Math.max(el.y - 16, 0) + 'px';
        label.style.background = color;
        label.style.color = 'white';
        label.style.fontSize = '11px';
        label.style.fontFamily = 'monospace';
        label.style.padding = '1px 4px';
        label.style.borderRadius = '2px';
        label.style.pointerEvents = 'none';
        overlay.appendChild(label);
    }
}
"""


class BrowserController:
    def __init__(
        self,
        storage_state_path: str | None = None,
        user_data_dir: str | None = None,
        show_debug_boxes: bool = False,
        browser_engine: str = "firefox",
    ):
        self.session = BrowserSession(
            storage_state_path=storage_state_path,
            user_data_dir=user_data_dir,
            browser_engine=browser_engine,
        )
        # Draws live numbered boxes directly on the page for YOU to watch,
        # completely independent of ENABLE_VISION in agent/graph.py (which
        # controls whether a screenshot gets sent to the LLM). This never
        # gets saved anywhere or sent anywhere — it's just a DOM overlay
        # with pointer-events:none, so it never blocks real clicks either.
        self.show_debug_boxes = show_debug_boxes

    @property
    def page(self):
        return self.session.page

    async def start(self, headless: bool = False):
        await self.session.start(headless=headless)

    async def stop(self):
        await self.session.stop()

    async def save_storage_state(self, path: str | None = None) -> str:
        """Saves the current login session (cookies + local storage) to
        disk for reuse on future runs. See save_login_session.py for the
        one-time interactive login flow that populates this file."""
        return await self.session.save_storage_state(path)

    async def goto(self, url: str) -> str:
        if not url.startswith("http"):
            url = "https://" + url
        await self.page.goto(url, wait_until="domcontentloaded")
        return f"Navigated to {url}"

    async def get_state(self) -> str:
        """Returns URL, title, and a numbered list of interactive elements."""
        await self.page.wait_for_timeout(500)  # let dynamic content settle
        elements = await self.page.evaluate(INDEX_SCRIPT)

        if self.show_debug_boxes:
            await self.page.evaluate(DRAW_DEBUG_OVERLAY_SCRIPT, elements)

        lines = [f"URL: {self.page.url}", f"Title: {await self.page.title()}", "Interactive elements:"]
        for el in elements:
            desc = f"[{el['index']}] <{el['tag']}"
            if el["type"]:
                desc += f" type={el['type']}"
            if el["role"]:
                desc += f" role={el['role']}"
            desc += f'> "{el["text"]}"'
            if el["href"]:
                desc += f" href={el['href'][:60]}"
            if el.get("disabled"):
                desc += " [disabled]"
            if el.get("checked") is True:
                desc += " [checked]"
            elif el.get("checked") is False:
                desc += " [unchecked]"
            if el.get("likelyUnread"):
                desc += " [likely-unread]"
            lines.append(desc)
        return "\n".join(lines)

    async def click(self, index: int) -> str:
        selector = f'[data-agent-index="{index}"]'
        succeeded = await safe_click(self.page, selector, retries=3, delay_ms=500)
        if not succeeded:
            raise Exception(f"Could not click element [{index}] after 3 attempts")
        return f"Clicked element [{index}]"

    async def type_text(self, index: int, text: str) -> str:
        el = self.page.locator(f'[data-agent-index="{index}"]')
        is_editable = await el.evaluate(
            "(node) => node.isContentEditable === true || node.getAttribute('contenteditable') === 'true'"
        )
        if is_editable:
            # Rich-text editors (Lexical, Draft.js — Facebook's composer
            # among them) keep their own internal state separate from the
            # raw DOM. Two things matter here:
            #   1. Reliably clear existing content via the editor's own
            #      selection/delete commands, not a keyboard shortcut —
            #      Ctrl+A can race with the editor still attaching its
            #      focus/selection handlers right after a click, silently
            #      selecting nothing and leaving old content in place
            #      (this is what caused text to appear duplicated).
            #   2. Type character-by-character afterward via real keyboard
            #      events with randomized delay, so it behaves like actual
            #      human typing rather than a single bulk value-set.
            await el.click(force=True)
            await self.page.wait_for_timeout(200)  # let the editor fully attach before clearing

            selector = f'[data-agent-index="{index}"]'
            await self.page.evaluate(
                """
                (selector) => {
                    const editor = document.querySelector(selector);
                    if (!editor) return;
                    editor.focus();
                    const selection = window.getSelection();
                    const range = document.createRange();
                    range.selectNodeContents(editor);
                    selection.removeAllRanges();
                    selection.addRange(range);
                    document.execCommand('delete', false, null);
                }
                """,
                selector,
            )

            for char in text:
                await self.page.keyboard.type(char, delay=random.randint(30, 90))

            return f"Typed '{text}' into contenteditable element [{index}] via human-like keystrokes"

        await el.fill(text, timeout=5000)
        return f"Typed '{text}' into element [{index}]"

    async def press_key(self, key: str) -> str:
        await self.page.keyboard.press(key)
        return f"Pressed key '{key}'"

    async def scroll(self, direction: str = "down") -> str:
        delta = 800 if direction == "down" else -800
        await self.page.mouse.wheel(0, delta)
        return f"Scrolled {direction}"

    async def click_at_coordinates(self, x: float, y: float) -> str:
        """Clicks at raw pixel coordinates via a real mouse event, bypassing
        DOM element lookup entirely. Used by the vision-fallback path
        (tools.py's click_vision_element) for elements that normal DOM
        indexing can't see at all — closed shadow roots, canvas-rendered
        UI, or DOM-restricted cross-origin iframes."""
        await self.page.mouse.click(x, y)
        return f"Clicked at pixel coordinates ({x:.0f}, {y:.0f})"

    async def screenshot_bytes(self) -> tuple[bytes, int, int]:
        """Raw PNG screenshot + its pixel dimensions, for OmniParser."""
        viewport = self.page.viewport_size
        png = await self.page.screenshot(type="png")
        return png, viewport["width"], viewport["height"]

    async def upload_file(self, file_path: str, index: int | None = None) -> str:
        """Attaches a local file, bypassing the OS file-picker dialog
        entirely (that dialog isn't part of the page and can't be
        automated the browser-click way).

        Preference order:
        1. An intercepted FileChooser, if the site's own "upload"/"photo"
           button was clicked and its onClick handler triggered the
           browser's native file dialog internally (common in modern SPAs
           like Facebook) — the session's filechooser listener captures
           this instead of letting the real OS window render.
        2. A specific element by index, if given.
        3. The first <input type="file"> on the page, as a fallback for
           simple sites that expose a real, unhidden input directly.
        """
        chooser = self.session.pending_file_chooser
        if chooser is not None:
            self.session.pending_file_chooser = None
            await chooser.set_files(file_path)
            return f"Attached file '{file_path}' via intercepted file-chooser dialog"

        if index is not None:
            el = self.page.locator(f'[data-agent-index="{index}"]')
        else:
            el = self.page.locator('input[type="file"]').first
        await el.set_input_files(file_path)
        return f"Attached file '{file_path}'"

    async def extract_text(self) -> str:
        text = await self.page.evaluate(
            """
            () => {
                const dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter((d) => {
                    const rect = d.getBoundingClientRect();
                    const style = window.getComputedStyle(d);
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                });
                // Multiple dialogs can exist in the DOM at once (some sites
                // keep hidden ones mounted) — the last one found is
                // typically the most recently opened / actually on top.
                const target = dialogs.length > 0 ? dialogs[dialogs.length - 1] : document.body;
                return target.innerText;
            }
            """
        )
        return text[:3000]

    async def screenshot_with_boxes(self) -> str:
        """Takes a screenshot and draws a labeled box around every element
        returned by get_state() (same indices) — the "Set-of-Mark" technique
        for a vision-capable model. Returns a base64-encoded PNG."""
        await self.page.wait_for_timeout(500)
        elements = await self.page.evaluate(INDEX_SCRIPT)
        png_bytes = await self.page.screenshot()

        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except Exception:
            font = ImageFont.load_default()

        palette = ["#FF3B30", "#34C759", "#007AFF", "#FF9500", "#AF52DE", "#00B8D9"]

        for el in elements:
            x, y, w, h = el["x"], el["y"], el["width"], el["height"]
            color = palette[el["index"] % len(palette)]
            draw.rectangle([x, y, x + w, y + h], outline=color, width=2)

            label = str(el["index"])
            bbox = draw.textbbox((0, 0), label, font=font)
            label_w, label_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            label_y = max(y - label_h - 4, 0)
            draw.rectangle([x, label_y, x + label_w + 4, label_y + label_h + 4], fill=color)
            draw.text((x + 2, label_y + 1), label, fill="white", font=font)

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
