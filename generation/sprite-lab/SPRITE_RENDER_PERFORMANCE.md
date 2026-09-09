# Sprite render performance

The normal sprite pipeline exports beauty, Freestyle lineart and projected
bones. These are the channels consumed by the web UI and AI source catalog.
It performs two render calls per cell instead of five. Set
`auxiliary_channels: true` in the sprite render payload to additionally export
segmentation, wireframe mesh, depth and pose heatmap. Direct Blender requests
accept the same option. Consumers of those optional channels must request them.

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
