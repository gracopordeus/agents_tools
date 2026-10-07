# Sprite render performance

The beauty worker now assigns neutral materials once for the entire sequence,
preserving their colors and avoiding per-cell material invalidation. Cycle
detection also reuses the initial deformed mesh signature across candidates.
Render metadata records preparation time, total time and elapsed time per cell.

Blender logs are streamed to disk while the process runs. GPU attempts that
produce no log progress for 60 seconds are stopped and enter the existing
software fallback/circuit breaker. `SPRITE_LAB_GPU_STALL_SECONDS` overrides
this interval for unusually expensive scenes. The total job timeout remains
in force for both backends. This is a log-activity watchdog, not a GPU profiler.

A two-cell software smoke render completed after the material change; its
first frame was pixel-identical to the existing render. Its timing overlapped
an active render and is not a valid throughput comparison. No sample count,
resolution, lighting or animation sampling quality was reduced.

The normal sprite pipeline renders only the beauty cell in Blender. After the
Blender worker exits, a local ControlNet annotator worker derives
`hed_softedge` for lineart and body-only `openpose` for bones, preserving the
same row/column grid. The annotators are loaded once per job and do not run SD
generation. The two channels can be processed concurrently with bounded CUDA
streams; production defaults to eight cell workers on the validated GPU. A
32-worker configuration exhausts VRAM during concurrent activations and is not
supported. `SPRITE_LAB_PYTHON` selects the Python environment;
`SPRITE_LAB_POSTPROCESS_DEVICE` selects `auto`, `cpu` or `cuda`.

The old Blender Freestyle lineart and projected-bones implementations remain
available only as legacy helper code and are no longer called by the normal
sprite worker. The current sprite output records the annotator provenance in
`controlnet_channels.json` and `render_metadata.json`.

Non-looping clips explicitly identified by catalog metadata skip geometric
cycle detection. Clips with unknown or looping metadata retain cycle detection.

`SPRITE_LAB_BLENDER_MODE` accepts `auto`, `gpu` or `software`. Auto attempts the
existing EEVEE GPU path and repeats the job with Mesa software rendering after
a worker failure. A failed GPU attempt opens a 24-hour circuit breaker stored
in `state/blender_gpu_health.json`, preventing every queued job from repeating
the same expensive driver crash. The local service uses auto mode. NVIDIA EGL
currently aborts with SIGABRT and CUDA enumeration stalled in diagnostic
probes, so the circuit breaker initially routes work through Mesa.

Validation: the UAL1 / great sword high spin attack composition completed a
one-cell render with beauty, lineart and bones. This establishes functional
coverage of the reduced pass set, not an end-to-end timing benchmark or a
validation of the full 64-cell sheet.
