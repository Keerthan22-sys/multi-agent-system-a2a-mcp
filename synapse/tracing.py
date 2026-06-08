# synapse/tracing.py — Centralized observability setup (Day 6).
#
# Wraps OpenTelemetry + OpenInference + Phoenix so every service can
# instrument itself in two lines:
#
#     from synapse.tracing import setup_tracing, tracer
#     setup_tracing("my-service")
#
# Then anywhere in the file:
#
#     with tracer.start_as_current_span("operation") as span:
#         span.set_attribute("key", value)
#         ...
#
# Designed to fail safe: if Phoenix isn't installed or isn't running,
# tracer becomes a no-op object. The system continues without traces.
import os
from contextlib import contextmanager

# Phoenix collector endpoint. Default matches `phoenix serve` defaults.
PHOENIX_COLLECTOR_ENDPOINT = os.getenv(
    "PHOENIX_COLLECTOR_ENDPOINT",
    "http://localhost:6006",
)

_tracer = None
_initialized = False


def setup_tracing(service_name: str):
    """
    Initialize Phoenix tracing for this service. Idempotent.
    Call once at module load — preferably before importing OpenAI.
    """
    global _tracer, _initialized
    if _initialized:
        return _tracer

    try:
        from phoenix.otel import register
        tracer_provider = register(
            project_name="synapse",
            endpoint=f"{PHOENIX_COLLECTOR_ENDPOINT}/v1/traces",
            auto_instrument=True,  # Auto-instruments OpenAI calls
            set_global_tracer_provider=False,  # Allow multiple services in one process
        )
        _tracer = tracer_provider.get_tracer(service_name)
        _initialized = True
        print(
            f"[tracing] Phoenix enabled for '{service_name}' "
            f"→ {PHOENIX_COLLECTOR_ENDPOINT}"
        )
    except ImportError:
        print(
            "[tracing] arize-phoenix-otel not installed; tracing disabled. "
            "Install with: pip install arize-phoenix arize-phoenix-otel "
            "openinference-instrumentation-openai"
        )
        _tracer = _NoOpTracer()
        _initialized = True
    except Exception as e:
        print(f"[tracing] Setup failed, continuing without traces: {e}")
        _tracer = _NoOpTracer()
        _initialized = True

    return _tracer


# ---------- No-op fallback so code never crashes when Phoenix is absent ----------

class _NoOpSpan:
    def set_attribute(self, *a, **kw): pass
    def set_attributes(self, *a, **kw): pass
    def set_status(self, *a, **kw): pass
    def record_exception(self, *a, **kw): pass
    def add_event(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(self, name, **kwargs):
        yield _NoOpSpan()


# Lazy module-level proxy so `from synapse.tracing import tracer` always works,
# even if setup_tracing() hasn't been called yet.
class _TracerProxy:
    def start_as_current_span(self, name, **kwargs):
        return (_tracer or _NoOpTracer()).start_as_current_span(name, **kwargs)

    def __getattr__(self, name):
        return getattr(_tracer or _NoOpTracer(), name)


tracer = _TracerProxy()