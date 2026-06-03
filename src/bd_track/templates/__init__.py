"""Packaged templates rendered with expandvars (POSIX-style ${VAR} interpolation).

Templates use ``${VAR}`` references only (not bare ``$VAR``); ``${VAR:-default}``
is supported. Unbound variables raise ``ValueError`` — typos fail loudly rather
than rendering as empty strings.

Layout:
  systemd/  — systemd unit templates (.service, .timer)
  envrc/    — direnv .envrc fragments
  sidecar/  — per-project .beads/bd-track.yaml scaffold
"""

from importlib.resources import files

from expandvars import UnboundVariable, expand


def render(path: str, **context: str) -> str:
    """Render a packaged template with ``${VAR}`` interpolation.

    Args:
        path: Path relative to this package, e.g.
            ``"systemd/bd-track-cleanup.service.tmpl"``.
        **context: Variables to interpolate. Unbound references raise.

    Returns:
        The rendered template text.

    Raises:
        ValueError: If the template references a variable not in `context`.
    """
    raw = (files("bd_track.templates") / path).read_text()
    try:
        return expand(raw, environ=context, nounset=True, surrounded_vars_only=True)
    except UnboundVariable as e:
        raise ValueError(f"Template {path} references unset variable: {e}") from e
