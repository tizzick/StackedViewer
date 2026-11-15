"""Safe wrappers around libmpv's render API.

python-mpv builds the parameter arrays for mpv_render_context_create() and
mpv_render_context_render() from temporary objects that Python frees before
libmpv reads them. The dangling pointers usually still hold the right bytes,
but not always: the symptom is tiles that randomly stay black. These wrappers
build the arrays by hand and keep every value alive for as long as it is used.
"""

from __future__ import annotations

import ctypes

from .libmpv import mpv


def _param_array(items):
    """items: list of (param name, ctypes object). Returns (array, keepalive)."""
    array = (mpv.MpvRenderParam * (len(items) + 1))()  # zero-filled; last entry terminates
    for i, (name, obj) in enumerate(items):
        array[i].type_id = mpv.MpvRenderParam.TYPES[name][0]
        array[i].data = ctypes.cast(ctypes.pointer(obj), ctypes.c_void_p)
    return array, [obj for _, obj in items]


class RenderContext:
    """An mpv OpenGL render context. Create and use with the GL context current."""

    def __init__(self, player, get_proc_address_fn):
        self._update_wrappers = []
        api_type = ctypes.create_string_buffer(b"opengl")
        init_params = mpv.MpvOpenGLInitParams(get_proc_address_fn)
        create_array, create_keep = _param_array([
            ("api_type", api_type),
            ("opengl_init_params", init_params),
        ])
        # "api_type" wants the char* itself, not a pointer to it.
        create_array[0].data = ctypes.cast(api_type, ctypes.c_void_p)

        self._handle_buf = ctypes.cast(
            ctypes.create_string_buffer(ctypes.sizeof(mpv.MpvRenderCtxHandle)),
            ctypes.POINTER(mpv.MpvRenderCtxHandle),
        )
        mpv._mpv_render_context_create(self._handle_buf, player.handle, create_array)
        self._handle = self._handle_buf.contents
        del create_keep

        # Parameters for render(), allocated once and reused for every frame.
        self._fbo = mpv.MpvOpenGLFBO(0, 0)
        self._flip_y = ctypes.c_int(1)
        self._block = ctypes.c_int(0)  # never wait inside mpv: tiles share one thread
        self._render_array, self._render_keep = _param_array([
            ("opengl_fbo", self._fbo),
            ("flip_y", self._flip_y),
            ("block_for_target_time", self._block),
        ])

    def set_update_callback(self, callback):
        """`callback()` is invoked on an mpv thread when a new frame is ready.

        The C-callable wrapper must outlive every possible call from mpv, so it
        is kept until this object is destroyed, which happens after free().
        """
        wrapper = mpv.RenderUpdateFn(lambda _userdata: callback())
        self._update_wrappers.append(wrapper)
        mpv._mpv_render_context_set_update_callback(self._handle, wrapper, None)

    def render(self, fbo: int, width: int, height: int):
        self._fbo.fbo = fbo
        self._fbo.w = width
        self._fbo.h = height
        self._fbo.internal_format = 0
        mpv._mpv_render_context_render(self._handle, self._render_array)

    def report_swap(self):
        mpv._mpv_render_context_report_swap(self._handle)

    def free(self):
        """Destroy the context. Blocks until mpv has stopped calling back."""
        if self._handle is not None:
            mpv._mpv_render_context_free(self._handle)
            self._handle = None
