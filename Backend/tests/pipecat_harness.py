"""Test helper: run one Pipecat processor and collect what comes out.

This mirrors pipecat.tests.utils.run_test with two changes:

  * a 15 second start timeout. The stock harness gives a pipeline only 1 second to start.
    Starting one takes about 1.6 s on a development laptop, so the stock harness fails with
    a TimeoutError here (and then hangs while cleaning up). 15 s is generous, not a delay:
    tests continue the instant the pipeline is up.
  * the runner is created with handle_sigint=False, the same as the production pipeline
    (LeadAI/voice/pipeline.py): a pipeline living inside a web server must not take over
    the process's signals.

Stand-in processors used with it must FORWARD frames (call push_frame); a bare FrameProcessor
subclass swallows the start signal and the pipeline never finishes starting.
"""
import asyncio

from pipecat.frames.frames import EndFrame, StartFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import QueuedFrameProcessor, SleepFrame
from pipecat.workers.runner import WorkerRunner

__all__ = ["SleepFrame", "run_stage"]


async def run_stage(processor, frames_to_send, *, send_end_frame=True, start_timeout=15.0):
    """Send frames through `processor`; return (downstream_frames, upstream_frames)."""
    received_up, received_down = asyncio.Queue(), asyncio.Queue()
    source = QueuedFrameProcessor(queue=received_up, queue_direction=FrameDirection.UPSTREAM)
    sink = QueuedFrameProcessor(queue=received_down, queue_direction=FrameDirection.DOWNSTREAM)
    worker = PipelineWorker(
        Pipeline([source, processor, sink]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        params=PipelineParams(),
    )
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def _on_started(worker, frame):
        started.set()

    async def push():
        await asyncio.wait_for(started.wait(), timeout=start_timeout)
        for frame in frames_to_send:
            if isinstance(frame, SleepFrame):
                await asyncio.sleep(frame.sleep)
            else:
                await worker.queue_frame(frame)
        if send_end_frame:
            await worker.queue_frame(EndFrame())

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await asyncio.gather(runner.run(), push())

    def drain(queue):
        frames = []
        while not queue.empty():
            frame = queue.get_nowait()
            if not isinstance(frame, (StartFrame, EndFrame)):
                frames.append(frame)
        return frames

    return drain(received_down), drain(received_up)
