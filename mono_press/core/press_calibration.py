"""Fixed P04 distance scale, independent of user recipe settings."""

PRESS_STEPS_PER_MM = 600

# Preserve the existing step-to-encoder and RPM conversion. This is an
# internal compatibility scale, not a newly measured motor specification.
PRESS_INTERNAL_STEPS_PER_REV = 3200
PRESS_INTERNAL_MM_PER_REV = PRESS_INTERNAL_STEPS_PER_REV / PRESS_STEPS_PER_MM
