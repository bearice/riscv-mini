"""AE350 boot image: the 20K framing and CRC rules with the AE350 load address.

The transport (12-word header, 128-byte packets carrying ``seq``/``len``/``crc32``
and ``K``+seq acknowledgements) is the shared one in ``boot_image``.  Only the
load address, the ABI tag and the platform identity differ, so the same host code
uploads to either platform and each side validates what it actually supports.
"""
import json
import zlib

from tools.boot_image import pack_image, unpack_image, packet, HEADER, CHUNK

from gateware.machines.mega138k_ae350 import address_map as ae350_map
LOAD = ae350_map.DDR_CODE
ABI_NAME = 'ae350-rv32ima-p2'
IMAGE_VERSION = 1


def abi_tag():
    """The platform's ABI tag, owned by the address/capability contract."""
    from gateware.machines.mega138k_ae350 import address_map as ae350_map
    return ae350_map.image_abi()


def build(payload):
    if not 4 <= len(payload) <= ae350_map.DDR_CODE_MAX:
        raise ValueError('AE350 payload exceeds the ROM loader range')
    return pack_image(payload, abi_tag(), load=LOAD)


def parse(image):
    header, payload = unpack_image(image, abi_tag(), load=LOAD)
    if len(payload) > ae350_map.DDR_CODE_MAX or header[6] != LOAD:
        raise ValueError('AE350 image differs from the ROM loader contract')
    return header, payload
