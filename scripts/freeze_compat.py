"""Build-time compatibility fixes for cx_Freeze module discovery."""


def patch_scanner():
    """Keep import arguments across EXTENDED_ARG in cx_Freeze's bytecode scanner."""
    from cx_Freeze import _bytecode

    if getattr(_bytecode.unpack_opargs, '_flatcam_extended_arg', False):
        return
    unpack_opargs = _bytecode.unpack_opargs

    def unpack_without_extended_arg(code):
        # The decoder already folds these bits into the next operand; the scanner would reset its from-list.
        for instruction in unpack_opargs(code):
            if instruction[2] != _bytecode.EXTENDED_ARG:
                yield instruction

    unpack_without_extended_arg._flatcam_extended_arg = True
    _bytecode.unpack_opargs = unpack_without_extended_arg
