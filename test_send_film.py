"""
Test client: behaves like a CT console and sends one test film to the print server.

    python test_send_film.py <server_ip> [port] [AE] [format]
    e.g. python test_send_film.py 127.0.0.1 104 EPSON_A3 STANDARD\\4,5
"""
import configparser
import os
import sys

import numpy as np
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from pynetdicom import AE
from pynetdicom.sop_class import (
    BasicFilmBox,
    BasicFilmSession,
    BasicGrayscaleImageBox,
    BasicGrayscalePrintManagementMeta,
    Printer,
    PrinterInstance,
)

_here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(_here, "config.ini"), encoding="utf-8")
host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
port = int(sys.argv[2]) if len(sys.argv) > 2 else _cfg.getint("dicom", "port", fallback=104)
aet = sys.argv[3] if len(sys.argv) > 3 else _cfg.get("dicom", "ae_title", fallback="EPSON_A3").strip()
fmt = sys.argv[4] if len(sys.argv) > 4 else "STANDARD\\3,4"
orientation = sys.argv[5] if len(sys.argv) > 5 else "PORTRAIT"

META = BasicGrayscalePrintManagementMeta


def phantom(n, size=512):
    """12-bit test image: a 'head' circle with a number of dots and a gradient."""
    y, x = np.mgrid[:size, :size]
    r = np.hypot(x - size / 2, y - size / 2)
    img = np.where(r < size * 0.42, 1500, 0).astype(np.float32)
    img += np.where(r < size * 0.38, 900, 0)
    img += (x / size) * 400
    for k in range(n):
        cx = size / 2 + np.cos(k) * size * 0.2
        cy = size / 2 + np.sin(k) * size * 0.2
        img[np.hypot(x - cx, y - cy) < 12] = 4095
    return np.clip(img, 0, 4095).astype("<u2")


ae = AE(ae_title="TEST_CT")
ae.add_requested_context(META)
assoc = ae.associate(host, port, ae_title=aet)
if not assoc.is_established:
    print("Association rejected/failed - is the server running? Check IP, port and AE title")
    if getattr(sys, "frozen", False):
        input("Press Enter to close...")
    sys.exit(1)

status, attrs = assoc.send_n_get([0x21100010], Printer, PrinterInstance, meta_uid=META)
print("Printer status:", status.Status if status else None, attrs.PrinterStatus if attrs else "")

fs = Dataset()
fs.NumberOfCopies = 1
fs.MediumType = "PAPER"
fs.FilmDestination = "MAGAZINE"
session_uid = generate_uid()
status, fs_rsp = assoc.send_n_create(fs, BasicFilmSession, session_uid, meta_uid=META)
print("Film session:", hex(status.Status))

fb = Dataset()
fb.ImageDisplayFormat = fmt
fb.FilmOrientation = orientation
fb.FilmSizeID = "14INX17IN"
fb.MagnificationType = "CUBIC"
ref = Dataset()
ref.ReferencedSOPClassUID = BasicFilmSession
ref.ReferencedSOPInstanceUID = session_uid
fb.ReferencedFilmSessionSequence = [ref]
fb_uid = generate_uid()
status, fb_rsp = assoc.send_n_create(fb, BasicFilmBox, fb_uid, meta_uid=META)
print("Film box:", hex(status.Status), "image boxes:", len(fb_rsp.ReferencedImageBoxSequence))

for pos, item in enumerate(fb_rsp.ReferencedImageBoxSequence, 1):
    arr = phantom(pos)
    ib = Dataset()
    ib.ImageBoxPosition = pos
    img = Dataset()
    img.SamplesPerPixel = 1
    img.PhotometricInterpretation = "MONOCHROME2"
    img.Rows, img.Columns = arr.shape
    img.PixelAspectRatio = [1, 1]
    img.BitsAllocated, img.BitsStored, img.HighBit = 16, 12, 11
    img.PixelRepresentation = 0
    img.PixelData = arr.tobytes()
    ib.BasicGrayscaleImageSequence = [img]
    status, _ = assoc.send_n_set(ib, BasicGrayscaleImageBox, item.ReferencedSOPInstanceUID, meta_uid=META)
    if status.Status != 0:
        print("Image box", pos, "status", hex(status.Status))

status, _ = assoc.send_n_action(None, 1, BasicFilmBox, fb_uid, meta_uid=META)
print("Print action:", hex(status.Status))
assoc.send_n_delete(BasicFilmBox, fb_uid, meta_uid=META)
assoc.release()
print("Done - check the printer and print_server.log")
if getattr(sys, "frozen", False):
    input("Press Enter to close...")
