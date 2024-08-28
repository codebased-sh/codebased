import ctypes


def get_capsule_pointer(capsule):
    # This is a highly unconventional and potentially unsafe method
    # It relies on CPython implementation details and may break
    # in different Python versions or implementations

    # Get the memory address of the capsule object
    capsule_address = id(capsule)

    # Create a ctypes structure to represent the PyObject
    class PyObject(ctypes.Structure):
        _fields_ = [("ob_refcnt", ctypes.c_ssize_t),
                    ("ob_type", ctypes.c_void_p)]

    # Create a ctypes structure to represent the PyCapsule
    class PyCapsule(ctypes.Structure):
        _fields_ = [("PyObject_HEAD", PyObject),
                    ("pointer", ctypes.c_void_p),
                    ("name", ctypes.c_char_p),
                    ("context", ctypes.c_void_p),
                    ("destructor", ctypes.c_void_p)]

    # Cast the capsule address to a PyCapsule pointer
    capsule_struct = ctypes.cast(capsule_address, ctypes.POINTER(PyCapsule)).contents

    # Extract the pointer value
    pointer_value = capsule_struct.pointer

    return pointer_value
