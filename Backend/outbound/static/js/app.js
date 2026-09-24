/**
 * Voice Agent - Main Application JavaScript
 */

// State
let callSid = null;
let ws = null;
let startTime = null;
let timer = null;
let xmlSections = [];
let xmlUploaded = false;
let currentScriptName = localStorage.getItem('currentScriptName') || '';
let originalFileName = localStorage.getItem('originalFileName') || '';
let sttLanguage = localStorage.getItem('sttLanguage') || 'hi';
let agentGender = localStorage.getItem('agentGender') || 'female';
let multiStt = localStorage.getItem('multiStt') === 'true';
let selectedSpeaker = localStorage.getItem('selectedSpeaker') || (agentGender === 'female' ? 'priya' : 'shubh');

// Sarvam Bulbul:v3 voices by gender
const SARVAM_VOICES_BY_GENDER = {
    female: [
        'ritu', 'priya', 'neha', 'pooja', 'simran', 'kavya', 'ishita', 'shreya', 'roopa',
        'amelia', 'sophia', 'ana'
    ],
    male: [
        'aditya', 'ashutosh', 'rahul', 'rohan', 'amit', 'dev', 'ratan', 'varun', 'manan',
        'sumit', 'kabir', 'aayan', 'shubh', 'advait'
    ]
};

// Speaker display names shown in the UI
const SPEAKER_DISPLAY = {
    // Female
    ritu: 'Ritu', priya: 'Priya', neha: 'Neha',
    pooja: 'Pooja', simran: 'Simran', kavya: 'Kavya',
    ishita: 'Ishita', shreya: 'Shreya', roopa: 'Roopa',
    amelia: 'Amelia', sophia: 'Sophia', ana: 'Ana',
    // Male
    aditya: 'Aditya', ashutosh: 'Ashutosh', rahul: 'Rahul',
    rohan: 'Rohan', amit: 'Amit', dev: 'Dev',
    ratan: 'Ratan', varun: 'Varun', manan: 'Manan',
    sumit: 'Sumit', kabir: 'Kabir', aayan: 'Aayan',
    shubh: 'Shubh', advait: 'Advait',
};

// DOM Elements
const $ = id => document.getElementById(id);
const statusDot = $('status-dot');
const statusText = $('status-text');
const duration = $('duration');
const list = $('transcript-list');
const empty = $('empty-state');
const liveDot = $('live-dot');
const btnCall = $('btn-call');
const btnHangup = $('btn-hangup');
const callForm = $('call-form');
const sectionsContainer = $('sections-container');
const noXmlState = $('no-xml-state');
const uploadArea = $('upload-area');
const uploadFilename = $('upload-filename');
const xmlFileInput = $('xml-file-input');
const scriptSelect = $('script-select');
const liveBadge = $('live-badge');
const btnDeleteScript = $('btn-delete-script');
const btnClear = $('btn-clear');
const btnSave = $('btn-save');
const btnSaveAs = $('btn-save-as');
const btnExport = $('btn-export');
const languageSelect = $('language-select');

// Utilities
const fmt = s => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
const time = () => new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

// Global fetch wrapper for authentication
async function fetchWithAuth(url, options = {}) {
    const token = localStorage.getItem('access_token');
    if (token) {
        options.headers = options.headers || {};
        options.headers['Authorization'] = `Bearer ${token}`;
    }
    try {
        const response = await fetch(url, options);
        if (response.status === 401) {
            // Token might be expired
            console.warn('Unauthorized request to', url);
            localStorage.removeItem('access_token');
            if (window.showLogin) {
                window.showLogin();
            } else {
                // Fallback but avoid loops
                if (!url.includes('/api/scripts')) {
                    window.location.reload();
                }
            }
        }
        return response;
    } catch (e) {
        console.error('Fetch error:', e);
        throw e;
    }
}

const modal = {
    overlay: $('modal-container'),
    title: $('modal-title'),
    message: $('modal-message'),
    input: $('modal-input'),
    inputContainer: $('modal-input-container'),
    confirmBtn: $('modal-confirm'),
    cancelBtn: $('modal-cancel'),
    closeBtn: $('modal-close'),
    reset: function () {
        this.overlay.classList.add('hidden');
        this.confirmBtn.onclick = null;
        this.cancelBtn.onclick = null;
        this.closeBtn.onclick = null;
        this.input.value = '';
        this.inputContainer.classList.add('hidden');
    }
};

function showModal({ title, message, showInput, confirmText, cancelText, onConfirm, onCancel }) {
    modal.title.textContent = title || 'Prompt';
    modal.message.textContent = message || '';
    if (showInput) {
        modal.inputContainer.classList.remove('hidden');
        modal.input.placeholder = showInput.placeholder || '';
        modal.input.value = showInput.value || '';
    } else {
        modal.inputContainer.classList.add('hidden');
    }
    modal.confirmBtn.textContent = confirmText || 'Confirm';
    modal.cancelBtn.textContent = cancelText || 'Cancel';
    modal.overlay.classList.remove('hidden');

    modal.confirmBtn.onclick = () => {
        const val = showInput ? modal.input.value : true;
        modal.reset();
        if (onConfirm) onConfirm(val);
    };
    modal.cancelBtn.onclick = () => {
        modal.reset();
        if (onCancel) onCancel();
    };
    modal.closeBtn.onclick = () => modal.reset();
}

function setStatus(s, t) {
    if (statusDot) statusDot.className = 'status-dot ' + s;
    if (statusText) statusText.textContent = t;
    if (liveDot) liveDot.classList.toggle('active', s === 'connected');
}

function addMsg(type, text) {
    if (empty) empty.style.display = 'none';
    const d = document.createElement('div');
    d.className = 'msg transcript-msg msg-' + type;
    if (type === 'system') {
        d.textContent = text;
    } else {
        d.innerHTML = text + '<div class="msg-time">' + (type === 'user' ? 'You' : 'Agent') + ' • ' + time() + '</div>';
    }
    list.appendChild(d);
    list.scrollTop = list.scrollHeight;
}

function startTimer() {
    startTime = Date.now();
    timer = setInterval(() => {
        duration.textContent = fmt(Math.floor((Date.now() - startTime) / 1000));
    }, 1000);
}

function stopTimer() {
    if (timer) {
        clearInterval(timer);
        timer = null;
    }
}

function endCall() {
    callSid = null;
    btnCall.disabled = !xmlUploaded;
    btnCall.classList.remove('hidden');
    btnHangup.classList.add('hidden');
    callForm.style.display = 'block';
    stopTimer();
    if (ws) {
        ws.close();
        ws = null;
    }
}

function connectWs(sid) {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = localStorage.getItem('access_token');
    const url = `${proto}//${location.host}/ws/transcript/${sid}${token ? '?token=' + token : ''}`;
    ws = new WebSocket(url);

    ws.onmessage = e => {
        const d = JSON.parse(e.data);
        if (d.type === 'user') {
            addMsg('user', d.text);
        } else if (d.type === 'agent') {
            addMsg('agent', d.text);
        } else if (d.type === 'status') {
            const s = (d.status || d.text || '').toLowerCase();
            if (s === 'connected') {
                setStatus('connected', 'Connected');
                startTimer();
            } else if (['ringing', 'initiated', 'in-progress'].includes(s)) {
                addMsg('system', s.charAt(0).toUpperCase() + s.slice(1).replace('-', ' '));
            } else if (['busy', 'failed', 'no-answer', 'canceled', 'completed', 'disconnected'].includes(s)) {
                const txt = s === 'no-answer' ? 'No Answer' : s.charAt(0).toUpperCase() + s.slice(1);
                setStatus('ended', txt);
                addMsg('system', txt);
                stopTimer();
                endCall();
            } else {
                addMsg('system', d.text || d.status);
            }
        }
    };
}

function updateCallButtonState() {
    btnCall.disabled = !xmlUploaded;
    if (!xmlUploaded) {
        btnCall.title = 'Upload or select a script first';
        if (empty) empty.querySelector('.empty-text').textContent = 'Upload XML to enable calling';
        // Hide edit buttons if nothing loaded
        if (btnSaveAs) btnSaveAs.classList.add('hidden');
        if (btnExport) btnExport.classList.add('hidden');
    } else {
        btnCall.title = '';
        if (empty) empty.querySelector('.empty-text').textContent = 'Ready to call';

        // SESSION MODE LOGIC:
        // If loaded for session (no currentScriptName but we have sections), 
        // hide Save As and Export to simplify, as "Save" handles both.
        if (xmlUploaded && !currentScriptName) {
            if (btnSaveAs) btnSaveAs.classList.add('hidden');
            if (btnExport) btnExport.classList.add('hidden');
            if (btnSave) btnSave.innerHTML = '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg> Save / Download';
        } else {
            // Library mode (has currentScriptName)
            if (btnSaveAs) btnSaveAs.classList.remove('hidden');
            if (btnExport) btnExport.classList.remove('hidden');
            if (btnSave) btnSave.innerHTML = '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg> Save';
        }
    }
}

// XML Upload
async function handleXmlUpload(file) {
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    const performUpload = async (saveToDisk) => {
        try {
            const response = await fetchWithAuth(`/api/upload-xml?save_to_disk=${saveToDisk}`, {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (response.ok && data.sections) {
                xmlSections = data.sections;
                xmlUploaded = true;

                if (saveToDisk) {
                    currentScriptName = data.filename;
                    originalFileName = data.filename;
                    localStorage.setItem('currentScriptName', currentScriptName);
                    localStorage.setItem('originalFileName', originalFileName);
                    await loadScripts();
                    scriptSelect.value = currentScriptName;
                } else {
                    // Just show on UI without adding to scripts dropdown
                    currentScriptName = '';
                    originalFileName = data.filename; // Track the name even if not saved
                    localStorage.removeItem('currentScriptName');
                    localStorage.setItem('originalFileName', originalFileName);
                    if (scriptSelect) scriptSelect.value = '';
                }
                renderSections();
                updateCallButtonState();
                uploadArea.style.borderColor = 'var(--success)';
                setTimeout(() => { uploadArea.style.borderColor = ''; }, 2000);
            } else {
                alert(data.detail || 'Failed to parse XML');
            }
        } catch (e) {
            alert('Upload failed: ' + e.message);
        }
    };

    showModal({
        title: 'Upload to Library?',
        message: `Do you want to save "${file.name}" permanently to the server library or just load it for this session?`,
        confirmText: 'Save to Library',
        cancelText: 'Load Session Only',
        onConfirm: () => performUpload(true),
        onCancel: () => performUpload(false)
    });
}

async function loadScripts() {
    try {
        const response = await fetchWithAuth('/api/scripts');
        const data = await response.json();
        if (response.ok && data.scripts) {
            const oldValue = currentScriptName;
            scriptSelect.innerHTML = '<option value="">Select a saved script...</option>';
            data.scripts.forEach(s => {
                const opt = document.createElement('option');
                opt.value = opt.textContent = s;
                scriptSelect.appendChild(opt);
            });

            if (oldValue && data.scripts.includes(oldValue)) {
                scriptSelect.value = oldValue;
                if (!xmlUploaded) loadScriptContent(oldValue);
            }
        }
    } catch (e) {
        console.error('Failed to load scripts:', e);
    }
}

async function loadScriptContent(filename) {
    if (!filename) {
        clearConfiguration(false); // Clear UI but don't ask
        return;
    }

    try {
        const response = await fetchWithAuth(`/api/scripts/${filename}`);
        const data = await response.json();
        if (response.ok && data.sections) {
            xmlSections = data.sections;
            xmlUploaded = true;
            currentScriptName = filename;
            originalFileName = filename;
            localStorage.setItem('currentScriptName', filename);
            localStorage.setItem('originalFileName', filename);
            renderSections();
            updateCallButtonState();
        }
    } catch (e) {
        alert('Failed to load script: ' + e.message);
    }
}

async function addNewScript() {
    showModal({
        title: 'Create New Script',
        message: 'Enter a filename for your new voice agent configuration:',
        showInput: { placeholder: 'my_agent.xml', value: 'new_agent.xml' },
        confirmText: 'Create',
        onConfirm: async (filename) => {
            if (!filename) return;
            if (!filename.endsWith('.xml')) filename += '.xml';

            try {
                const responseTmp = await fetch('/static/agent_template_blank.xml');
                const xmlText = await responseTmp.text();

                const blob = new Blob([xmlText], { type: 'text/xml' });
                const formData = new FormData();
                formData.append('file', blob, filename);

                const response = await fetchWithAuth('/api/upload-xml?save_to_disk=true', {
                    method: 'POST',
                    body: formData
                });

                if (response.ok) {
                    await loadScripts();
                    scriptSelect.value = filename;
                    await loadScriptContent(filename);
                } else {
                    const data = await response.json();
                    alert('Failed to create script: ' + (data.detail || 'Unknown error'));
                }
            } catch (e) {
                alert('Error creating script: ' + e.message);
            }
        }
    });
}

async function deleteScript() {
    const filename = scriptSelect.value;
    if (!filename) {
        alert('Please select a script to delete');
        return;
    }

    showModal({
        title: 'Delete Script',
        message: `Are you sure you want to permanently delete "${filename}"? This action cannot be undone.`,
        confirmText: 'Delete Forever',
        onConfirm: async (confirmed) => {
            if (!confirmed) return;
            try {
                const response = await fetchWithAuth(`/api/scripts/${filename}`, { method: 'DELETE' });
                if (response.ok) {
                    if (currentScriptName === filename) {
                        xmlSections = [];
                        xmlUploaded = false;
                        currentScriptName = '';
                        localStorage.removeItem('currentScriptName');
                        renderSections();
                        updateCallButtonState();
                    }
                    await loadScripts();
                } else {
                    const data = await response.json();
                    alert(data.detail || 'Delete failed');
                }
            } catch (e) {
                alert('Delete failed: ' + e.message);
            }
        }
    });
}

function clearConfiguration(ask = true) {
    if (ask) {
        showModal({
            title: 'Clear Configuration',
            message: 'Are you sure you want to clear current edits? This will not delete any files from the server library.',
            confirmText: 'Clear View',
            onConfirm: () => {
                currentScriptName = '';
                xmlSections = [];
                xmlUploaded = false;
                localStorage.removeItem('currentScriptName');
                if (scriptSelect) scriptSelect.value = '';
                renderSections();
                updateCallButtonState();
            }
        });
    } else {
        currentScriptName = '';
        xmlSections = [];
        xmlUploaded = false;
        renderSections();
        updateCallButtonState();
    }
}

function renderSections() {
    if (!sectionsContainer) return;

    sectionsContainer.innerHTML = '';

    if (xmlSections.length === 0) {
        noXmlState.classList.remove('hidden');
        return;
    }

    noXmlState.classList.add('hidden');

    xmlSections.forEach((section, index) => {
        const card = createSectionCard(section, index);
        sectionsContainer.appendChild(card);
    });

    // Add "Add Section" button
    const addBtn = document.createElement('button');
    addBtn.className = 'add-section-btn';
    addBtn.innerHTML = '+ Add New Section';
    addBtn.onclick = () => addNewSection();
    sectionsContainer.appendChild(addBtn);
}

function createSectionCard(section, index) {
    const card = document.createElement('div');
    card.className = 'section-card';
    card.dataset.index = index;
    card.draggable = true;

    const contentText = formatSectionContent(section);

    card.innerHTML = `
        <div class="section-header">
            <div class="drag-handle" title="Drag to reorder">⋮⋮</div>
            <div style="display: flex; align-items: center; gap: 10px; flex: 1; cursor: pointer;" onclick="toggleSection(${index})">
                <span class="section-title">${section.title}</span>
                <span class="section-type">${section.type}</span>
            </div>
            <button onclick="deleteSection(${index})" class="delete-btn" title="Delete section">×</button>
        </div>
        <div class="section-content" id="section-content-${index}">
            <textarea class="section-textarea" 
                      data-index="${index}"
                      oninput="updateSection(${index}, this.value)">${contentText}</textarea>
        </div>
    `;

    // Drag events
    card.ondragstart = (e) => {
        e.dataTransfer.setData('text/plain', index);
        card.classList.add('dragging');
    };
    card.ondragend = () => card.classList.remove('dragging');
    card.ondragover = (e) => {
        e.preventDefault();
        card.classList.add('drag-over');
    };
    card.ondragleave = () => card.classList.remove('drag-over');
    card.ondrop = (e) => {
        e.preventDefault();
        card.classList.remove('drag-over');
        const fromIndex = parseInt(e.dataTransfer.getData('text/plain'));
        const toIndex = index;
        if (fromIndex !== toIndex) {
            const [moved] = xmlSections.splice(fromIndex, 1);
            xmlSections.splice(toIndex, 0, moved);
            renderSections();
        }
    };

    return card;
}

function formatSectionContent(section) {
    if (section.rawContent !== undefined && section.rawContent !== null) {
        return section.rawContent;
    }
    const content = section.content;
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
        if (section.type === 'identity' || section.type === 'data') {
            return content.map(f => `${f.name || f.label}: ${f.value}`).join('\n');
        } else if (section.type === 'list' || section.type === 'data-unavailable') {
            return content.map(i => typeof i === 'string' ? i : (i.text || '')).join('\n');
        } else if (section.type === 'scenarios') {
            return content.map(s => `IF: ${s.if}\nTHEN: ${s.then}`).join('\n\n');
        } else if (section.type === 'script') {
            return content.map(s => `${s.order}. ${s.text}`).join('\n');
        } else if (section.type === 'features') {
            return content.map(f => `${f.name}: ${f.enabled ? 'enabled' : 'disabled'}`).join('\n');
        }
        return JSON.stringify(content, null, 2);
    }
    if (typeof content === 'object') {
        if (section.type === 'rules') {
            let text = 'MUST DO:\n';
            text += (content.must_do || []).map(i => `- ${i}`).join('\n');
            text += '\n\nMUST NOT:\n';
            text += (content.must_not || []).map(i => `- ${i}`).join('\n');
            return text;
        } else if (section.type === 'personality') {
            return `Tones: ${(content.tones || []).join(', ')}\nStyle: ${content.style}\nFiller words: ${content.filler_words}`;
        }
        return JSON.stringify(content, null, 2);
    }
    return String(content);
}

function toggleSection(index) {
    const content = $(`section-content-${index}`);
    if (content) content.style.display = content.style.display === 'none' ? 'block' : 'none';
}

function updateSection(index, value) {
    if (xmlSections[index]) {
        xmlSections[index].rawContent = value;

        // Visual feedback for Auto-Sync
        if (liveBadge) {
            liveBadge.textContent = 'Auto-Sync: Syncing...';
            liveBadge.style.opacity = '0.7';

            clearTimeout(window.syncTimeout);
            window.syncTimeout = setTimeout(() => {
                liveBadge.textContent = 'Auto-Sync: Ready';
                liveBadge.style.opacity = '1';
            }, 600);
        }
    }
}

function addNewSection() {
    const sectionTypes = {
        'text': { label: 'Text', template: 'Content...' },
        'list': { label: 'List', template: '- Item' },
        'rules': { label: 'Rules', template: 'MUST DO:\n- \nMUST NOT:\n- ' },
        'identity': { label: 'Identity', template: 'name: \nrole: ' }
    };

    const modalDiv = document.createElement('div');
    modalDiv.className = 'modal-overlay'; // Reusing the high-priority centering class

    modalDiv.innerHTML = `
        <div class="modal-box">
            <button id="btn-modal-close" class="modal-close" title="Close">&times;</button>
            <h3 class="modal-title">Add New Section</h3>
            <p class="modal-message">Choose a title and type for the new configuration block.</p>
            
            <div class="modal-input-container">
                <input type="text" id="new-sec-title" class="modal-field" placeholder="Section Title (e.g. Greeting)">
                <select id="new-sec-type" class="modal-field">
                    ${Object.entries(sectionTypes).map(([k, v]) => `<option value="${k}">${v.label}</option>`).join('')}
                </select>
            </div>

            <div class="modal-actions">
                <button id="btn-modal-cancel" class="btn btn-secondary">Cancel</button>
                <button id="btn-modal-add" class="btn btn-primary">Add Section</button>
            </div>
        </div>
    `;
    document.body.appendChild(modalDiv);

    modalDiv.querySelector('#btn-modal-close').onclick = () => modalDiv.remove();
    modalDiv.querySelector('#btn-modal-cancel').onclick = () => modalDiv.remove();
    modalDiv.querySelector('#btn-modal-add').onclick = () => {
        const title = $('new-sec-title').value.trim();
        if (!title) return alert('Title required');
        xmlSections.push({
            id: title.toLowerCase().replace(/\s+/g, '_'),
            title: title,
            type: $('new-sec-type').value,
            content: sectionTypes[$('new-sec-type').value].template,
            editable: true
        });
        modalDiv.remove();
        renderSections();
    };
}

function deleteSection(index) {
    xmlSections.splice(index, 1);
    renderSections();
}

async function saveSections(asNew = false) {
    // Case 1: Session-Only script (not in server library)
    if (!asNew && !currentScriptName && originalFileName) {
        showModal({
            title: 'Update Session File',
            message: `You are editing "${originalFileName}" (Loaded for Session). How would you like to save your changes?`,
            confirmText: 'Download (Save to Computer)',
            cancelText: 'Add to Server Library',
            onConfirm: () => exportXml(),
            onCancel: () => {
                // Secondary modal to ask for filename when saving to library
                showModal({
                    title: 'Add to Library',
                    message: 'Enter a filename to save this script permanently on the server:',
                    showInput: { placeholder: 'my_agent.xml', value: originalFileName },
                    confirmText: 'Save to Library',
                    onConfirm: (name) => {
                        if (name) executeSave(name);
                    }
                });
            }
        });
        return;
    }

    // Case 2: New script or empty state
    if (asNew || !currentScriptName) {
        showModal({
            title: asNew ? 'Save As...' : 'Save Script',
            message: 'Enter a filename to save your script permanently:',
            showInput: { placeholder: 'my_agent.xml', value: currentScriptName || originalFileName || 'my_agent.xml' },
            confirmText: 'Save to Library',
            onConfirm: (filename) => {
                if (filename) executeSave(filename);
            }
        });
    } else {
        // Case 3: Editing existing library script
        executeSave(currentScriptName);
    }
}

async function executeSave(filename) {
    if (!filename.endsWith('.xml')) filename += '.xml';

    try {
        const originalText = btnSave.innerHTML;
        btnSave.disabled = btnSaveAs.disabled = true;
        btnSave.innerHTML = 'Saving...';

        const response = await fetchWithAuth('/api/save-xml', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                sections: xmlSections,
                filename: filename
            })
        });

        if (response.ok) {
            currentScriptName = filename;
            originalFileName = filename;
            localStorage.setItem('currentScriptName', filename);
            localStorage.setItem('originalFileName', filename);
            btnSave.innerHTML = '✓ Saved';
            btnSave.style.color = 'var(--success)';
            setTimeout(() => {
                btnSave.innerHTML = '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24" width="16" height="16"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" /></svg> Save';
                btnSave.style.color = '';
                btnSave.disabled = btnSaveAs.disabled = false;
            }, 1500);
            await loadScripts();
            scriptSelect.value = filename;
            updateCallButtonState();
        } else {
            const data = await response.json();
            alert('Error: ' + (data.detail || 'Save failed'));
            btnSave.innerHTML = 'Error';
            setTimeout(() => { btnSave.innerHTML = originalText; btnSave.disabled = btnSaveAs.disabled = false; }, 2000);
        }
    } catch (e) {
        alert('Save failed: ' + e.message);
        btnSave.disabled = btnSaveAs.disabled = false;
    }
}

async function exportXml() {
    if (!xmlSections.length) return alert('No configuration to export');

    try {
        const response = await fetchWithAuth('/api/save-xml', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                sections: xmlSections,
                filename: 'export_tmp.xml'
            })
        });

        if (response.ok) {
            const link = document.createElement('a');
            link.href = '/api/download-script/export_tmp.xml';
            link.download = currentScriptName || originalFileName || 'agent_config.xml';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    } catch (e) {
        alert('Export failed: ' + e.message);
    }
}

// Make call
async function makeCall() {
    const phone = $('phone-number').value;
    if (!phone) return alert('Enter a phone number');
    if (!xmlUploaded) return alert('Please load a script first');

    btnCall.disabled = true;
    setStatus('calling', 'Calling');
    list.querySelectorAll('.msg').forEach(m => m.remove());
    if (empty) empty.style.display = 'flex';
    callForm.style.display = 'none';

    try {
        const response = await fetchWithAuth('/api/make-call', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                phone_number: phone,
                language: sttLanguage,
                gender: agentGender,
                speaker: selectedSpeaker,
                multi_stt: multiStt,
                sections: xmlSections
            })
        });

        const data = await response.json();
        if (response.ok && data.call_sid) {
            callSid = data.call_sid;
            btnCall.classList.add('hidden');
            btnHangup.classList.remove('hidden');
            btnHangup.disabled = false;
            addMsg('system', 'Calling ' + data.phone_number);
            connectWs(data.call_sid);
        } else {
            alert(data.detail || 'Call failed');
            btnCall.disabled = false;
            callForm.style.display = 'block';
            setStatus('', 'Ready');
        }
    } catch (e) {
        alert(e.message);
        btnCall.disabled = false;
        callForm.style.display = 'block';
        setStatus('', 'Ready');
    }
}

async function hangup() {
    if (!callSid) return;
    btnHangup.disabled = true;
    try {
        await fetchWithAuth('/api/hangup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ call_sid: callSid })
        });
    } catch (e) { }
    setStatus('ended', 'Ended');
    addMsg('system', 'Call ended');
    endCall();
}

function initEventListeners() {
    if (uploadArea) uploadArea.onclick = () => xmlFileInput.click();
    if (xmlFileInput) xmlFileInput.onchange = (e) => { handleXmlUpload(e.target.files[0]); e.target.value = ''; };

    if (uploadArea) {
        uploadArea.ondragover = (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); };
        uploadArea.ondragleave = () => uploadArea.classList.remove('dragover');
        uploadArea.ondrop = (e) => {
            e.preventDefault(); uploadArea.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file && file.name.endsWith('.xml')) handleXmlUpload(file);
            else alert('Please upload an XML file');
        };
    }

    if (scriptSelect) scriptSelect.onchange = (e) => loadScriptContent(e.target.value);
    if ($('btn-add-script')) $('btn-add-script').onclick = addNewScript;
    if (btnDeleteScript) btnDeleteScript.onclick = deleteScript;
    if (btnSave) btnSave.onclick = () => saveSections(false);
    if (btnSaveAs) btnSaveAs.onclick = () => saveSections(true);
    if ($('btn-export')) $('btn-export').onclick = exportXml;
    if (btnClear) btnClear.onclick = () => clearConfiguration(true);
    if (btnCall) btnCall.onclick = makeCall;
    if (btnHangup) btnHangup.onclick = hangup;
    if (languageSelect) {
        languageSelect.value = sttLanguage;
        languageSelect.onchange = (e) => {
            sttLanguage = e.target.value;
            localStorage.setItem('sttLanguage', sttLanguage);
        };
    }

    // Smart Multi-lingual toggle logic
    const multiSttToggle = $('multi-stt-toggle');
    if (multiSttToggle) {
        multiSttToggle.checked = multiStt;
        multiSttToggle.onchange = (e) => {
            multiStt = e.target.checked;
            localStorage.setItem('multiStt', multiStt);
        };
    }

    function populateSpeakerSelect(gender) {
        const select = $('speaker-select');
        if (!select) return;

        select.innerHTML = '';
        const voices = SARVAM_VOICES_BY_GENDER[gender] || [];

        voices.forEach(voiceId => {
            const opt = document.createElement('option');
            opt.value = voiceId;
            opt.textContent = SPEAKER_DISPLAY[voiceId] || voiceId;
            select.appendChild(opt);
        });

        // Restore selection if valid for this gender, else pick first
        if (voices.includes(selectedSpeaker)) {
            select.value = selectedSpeaker;
        } else {
            selectedSpeaker = voices[0];
            select.value = selectedSpeaker;
            localStorage.setItem('selectedSpeaker', selectedSpeaker);
        }
    }

    // Gender toggle logic
    const genderBtns = document.querySelectorAll('.gender-btn');
    const speakerSelect = $('speaker-select');

    function updateGenderUI(gender) {
        agentGender = gender;
        localStorage.setItem('agentGender', gender);
        genderBtns.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.gender === gender);
        });
        populateSpeakerSelect(gender);
    }

    if (speakerSelect) {
        speakerSelect.onchange = (e) => {
            selectedSpeaker = e.target.value;
            localStorage.setItem('selectedSpeaker', selectedSpeaker);
        };
    }

    genderBtns.forEach(btn => {
        btn.addEventListener('click', () => updateGenderUI(btn.dataset.gender));
    });
    // Initialize UI state
    updateGenderUI(agentGender);
}

// Expose initialization globally so it can be re-run after login
window.initApp = function() {
    console.log('Initializing Voice Bot Data...');
    loadScripts();
    updateCallButtonState();
};

document.addEventListener('DOMContentLoaded', () => {
    console.log('Voice Bot UI v1.6.1 Loaded');
    initEventListeners();
    
    // Only attempt to load scripts if we think we have a token
    if (localStorage.getItem('access_token')) {
        window.initApp();
    }
});
