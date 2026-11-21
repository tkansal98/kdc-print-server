"""
KDC DICOM Print Server
======================
Receives DICOM Print jobs (Basic Grayscale / Color Print Management) from a
CT/MRI console (e.g. GE BrightSpeed / ADW) and prints each film on an ordinary
Windows printer such as the Epson EcoTank L18050 (A3).

Run:  python kdc_print_server.py            (uses config.ini next to this file)
      python kdc_print_server.py --no-print (save images only, for testing)
"""

import argparse
import configparser
import datetime as dt
import logging
import os
import queue
import shutil
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydicom.dataset import Dataset
from pydicom.uid import generate_uid
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    BasicColorImageBox,
    BasicColorPrintManagementMeta,
    BasicFilmBox,
    BasicFilmSession,
    BasicGrayscaleImageBox,
    BasicGrayscalePrintManagementMeta,
    Printer,
    PrinterInstance,
    Verification,
)

APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
VERSION = "1.0"

# Paper sizes in mm (portrait) and the matching Windows DMPAPER_* code
PAPERS = {
    "A3": ((297.0, 420.0), 8),
    "A4": ((210.0, 297.0), 9),
    "A3+": ((329.0, 483.0), 8),       # printer driver may expose this as a custom size
    "11X17": ((279.4, 431.8), 3),
    "LETTER": ((215.9, 279.4), 1),
}

log = logging.getLogger("kdc-print")


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
DEFAULT_CONFIG = """
[dicom]
ae_title = EPSON_A3
port = 104
require_called_ae = no

[printer]
name =
paper = A3
orientation = auto
auto_print = yes
max_copies = 5

[page]
dpi = 300
background = black
margin_mm = 4
gap_mm = 1.5
header_text = Kansal Diagnostic Centre, Amritsar
header_height_mm = 7
show_datetime = yes

[output]
folder = prints
save_png = yes
save_pdf = no
keep_days = 30
"""


def load_config(path):
    cfg = configparser.ConfigParser()
    cfg.read_string(DEFAULT_CONFIG)
    if os.path.exists(path):
        cfg.read(path, encoding="utf-8")
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG.lstrip())
    return cfg


# --------------------------------------------------------------------------
# Image handling
# --------------------------------------------------------------------------
def dataset_to_image(item, polarity="NORMAL"):
    """Convert a Basic Grayscale/Color Image Sequence item to a PIL image."""
    rows, cols = int(item.Rows), int(item.Columns)
    spp = int(getattr(item, "SamplesPerPixel", 1))
    ba = int(getattr(item, "BitsAllocated", 8))
    bs = int(getattr(item, "BitsStored", ba))
    pi = str(getattr(item, "PhotometricInterpretation", "MONOCHROME2")).upper()
    raw = item.PixelData

    if spp == 3:
        arr = np.frombuffer(raw, dtype=np.uint8)[: rows * cols * 3]
        if int(getattr(item, "PlanarConfiguration", 0)) == 1:
            arr = arr.reshape(3, rows, cols).transpose(1, 2, 0)
        else:
            arr = arr.reshape(rows, cols, 3)
        img = Image.fromarray(np.ascontiguousarray(arr), "RGB")
    else:
        if ba == 16:
            arr = np.frombuffer(raw, dtype="<u2")[: rows * cols].reshape(rows, cols)
            maxval = (1 << bs) - 1
            arr = (arr & maxval).astype(np.float32) * (255.0 / maxval)
            arr = np.clip(arr + 0.5, 0, 255).astype(np.uint8)
        else:
            arr = np.frombuffer(raw, dtype=np.uint8)[: rows * cols].reshape(rows, cols)
        if pi == "MONOCHROME1":
            arr = 255 - arr
        img = Image.fromarray(np.ascontiguousarray(arr), "L")

    if str(polarity).upper() == "REVERSE":
        img = Image.eval(img, lambda v: 255 - v)
    return img


def parse_display_format(fmt):
    """Return (kind, counts) for an Image Display Format.

    kind 'row': counts[i] = number of images in row i
    kind 'col': counts[i] = number of images in column i
    """
    fmt = (fmt or "STANDARD\\1,1").upper().replace(" ", "")
    parts = fmt.split("\\")
    kind, args = parts[0], parts[1] if len(parts) > 1 else ""
    try:
        if kind == "STANDARD":
            c, r = (int(x) for x in args.split(","))
            return "row", [c] * r
        if kind == "ROW":
            return "row", [int(x) for x in args.split(",")]
        if kind == "COL":
            return "col", [int(x) for x in args.split(",")]
    except ValueError:
        pass
    log.warning("Unsupported display format %r, using 1x1", fmt)
    return "row", [1]


def layout_cells(fmt, x0, y0, w, h, gap):
    """Return a list of (x, y, w, h) cells in Image Box Position order."""
    kind, counts = parse_display_format(fmt)
    cells = []
    n = len(counts)
    if kind == "row":
        rh = (h - gap * (n - 1)) / n
        for i, c in enumerate(counts):
            cw = (w - gap * (c - 1)) / c
            for j in range(c):
                cells.append((x0 + j * (cw + gap), y0 + i * (rh + gap), cw, rh))
    else:
        cw = (w - gap * (n - 1)) / n
        for j, r in enumerate(counts):
            rh = (h - gap * (r - 1)) / r
            for i in range(r):
                cells.append((x0 + j * (cw + gap), y0 + i * (rh + gap), cw, rh))
    return cells


def count_boxes(fmt):
    return sum(parse_display_format(fmt)[1])


RESAMPLE = {
    "REPLICATE": Image.NEAREST,
    "BILINEAR": Image.BILINEAR,
    "CUBIC": Image.BICUBIC,
    "NONE": Image.BICUBIC,
}


def _font(size):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_film(film, cfg, film_no=1):
    """Compose one film box into a page-sized PIL image."""
    pg = cfg["page"]
    paper = cfg["printer"].get("paper", "A3").upper()
    (pw_mm, ph_mm), _ = PAPERS.get(paper, PAPERS["A3"])

    orient = cfg["printer"].get("orientation", "auto").lower()
    if orient == "auto":
        orient = str(film["attrs"].get("FilmOrientation", "PORTRAIT")).lower()
    landscape = orient.startswith("land")
    if landscape:
        pw_mm, ph_mm = ph_mm, pw_mm

    dpi = pg.getint("dpi", 300)
    mm = dpi / 25.4
    W, H = int(pw_mm * mm), int(ph_mm * mm)
    dark = pg.get("background", "black").lower() != "white"
    bg, fg = (0, (235, 235, 235)) if dark else (255, (20, 20, 20))
    page = Image.new("RGB", (W, H), (bg, bg, bg))
    draw = ImageDraw.Draw(page)

    margin = pg.getfloat("margin_mm", 4) * mm
    gap = pg.getfloat("gap_mm", 1.5) * mm

    header = pg.get("header_text", "").strip()
    show_dt = pg.getboolean("show_datetime", True)
    hh = pg.getfloat("header_height_mm", 7) * mm if (header or show_dt) else 0
    if hh:
        font = _font(int(hh * 0.6))
        ty = margin + hh * 0.15
        if header:
            draw.text((margin, ty), header, fill=fg, font=font)
        if show_dt:
            stamp = dt.datetime.now().strftime("%d-%b-%Y %H:%M") + f"   Film {film_no}"
            tw = draw.textlength(stamp, font=font)
            draw.text((W - margin - tw, ty), stamp, fill=fg, font=font)

    area_y = margin + hh
    cells = layout_cells(film["attrs"].get("ImageDisplayFormat", "STANDARD\\1,1"),
                         margin, area_y, W - 2 * margin, H - area_y - margin, gap)
    film_mag = str(film["attrs"].get("MagnificationType", "CUBIC")).upper()

    for box in film["boxes"]:
        img = box.get("image")
        pos = box.get("pos")
        if img is None or not pos or pos > len(cells):
            continue
        x, y, cw, ch = cells[pos - 1]
        scale = min(cw / img.width, ch / img.height)
        nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        mag = str(box.get("mag") or film_mag).upper()
        im = img.resize((nw, nh), RESAMPLE.get(mag, Image.BICUBIC)).convert("RGB")
        page.paste(im, (int(x + (cw - nw) / 2), int(y + (ch - nh) / 2)))
    return page, landscape


# --------------------------------------------------------------------------
# Printing (Windows GDI) and saving
# --------------------------------------------------------------------------
def print_windows(img, cfg, landscape, copies, jobname):
    import win32con
    import win32gui
    import win32print
    import win32ui
    from PIL import ImageWin

    name = cfg["printer"].get("name", "").strip() or win32print.GetDefaultPrinter()
    paper = cfg["printer"].get("paper", "A3").upper()
    h = win32print.OpenPrinter(name)
    try:
        devmode = win32print.GetPrinter(h, 2)["pDevMode"]
    finally:
        win32print.ClosePrinter(h)
    devmode.PaperSize = PAPERS.get(paper, PAPERS["A3"])[1]
    devmode.Orientation = win32con.DMORIENT_LANDSCAPE if landscape else win32con.DMORIENT_PORTRAIT
    devmode.Fields |= win32con.DM_PAPERSIZE | win32con.DM_ORIENTATION

    hdc = win32gui.CreateDC("WINSPOOL", name, devmode)
    dc = win32ui.CreateDCFromHandle(hdc)
    try:
        pw, ph = dc.GetDeviceCaps(win32con.HORZRES), dc.GetDeviceCaps(win32con.VERTRES)
        scale = min(pw / img.width, ph / img.height)
        w, hgt = int(img.width * scale), int(img.height * scale)
        x, y = (pw - w) // 2, (ph - hgt) // 2
        dib = ImageWin.Dib(img)
        dc.StartDoc(jobname)
        for _ in range(copies):
            dc.StartPage()
            dib.draw(dc.GetHandleOutput(), (x, y, x + w, y + hgt))
            dc.EndPage()
        dc.EndDoc()
    finally:
        dc.DeleteDC()
    log.info("Sent to printer '%s' (%s, %s, %d copies)", name, paper,
             "landscape" if landscape else "portrait", copies)


class PrintWorker(threading.Thread):
    def __init__(self, cfg, do_print):
        super().__init__(daemon=True)
        self.cfg, self.do_print = cfg, do_print
        self.q = queue.Queue()
        self.out_root = cfg["output"].get("folder", "prints")
        if not os.path.isabs(self.out_root):
            self.out_root = os.path.join(APP_DIR, self.out_root)

    def submit(self, film, copies, film_no):
        self.q.put((film, copies, film_no))

    def run(self):
        while True:
            film, copies, film_no = self.q.get()
            try:
                self.handle(film, copies, film_no)
            except Exception:
                log.exception("Failed to render/print film")

    def handle(self, film, copies, film_no):
        page, landscape = render_film(film, self.cfg, film_no)
        out = self.cfg["output"]
        day = os.path.join(self.out_root, dt.date.today().isoformat())
        os.makedirs(day, exist_ok=True)
        stem = os.path.join(day, dt.datetime.now().strftime("%H%M%S_%f")[:-3] + f"_film{film_no}")
        dpi = self.cfg["page"].getint("dpi", 300)
        if out.getboolean("save_png", True):
            page.save(stem + ".png", dpi=(dpi, dpi), optimize=False, compress_level=3)
            log.info("Saved %s.png", stem)
        if out.getboolean("save_pdf", False):
            page.save(stem + ".pdf", resolution=dpi)
            log.info("Saved %s.pdf", stem)
        if self.do_print and self.cfg["printer"].getboolean("auto_print", True):
            if sys.platform == "win32":
                print_windows(page, self.cfg, landscape, copies, f"DICOM film {film_no}")
            else:
                log.warning("Printing is only supported on Windows; image saved only")

    def cleanup_old(self):
        keep = self.cfg["output"].getint("keep_days", 30)
        if keep <= 0 or not os.path.isdir(self.out_root):
            return
        cutoff = dt.date.today() - dt.timedelta(days=keep)
        for d in os.listdir(self.out_root):
            try:
                if dt.date.fromisoformat(d) < cutoff:
                    shutil.rmtree(os.path.join(self.out_root, d), ignore_errors=True)
            except ValueError:
                continue


# --------------------------------------------------------------------------
# DICOM Print SCP
# --------------------------------------------------------------------------
class PrintSCP:
    def __init__(self, cfg, worker):
        self.cfg, self.worker = cfg, worker
        self.lock = threading.Lock()
        self.sessions = {}   # uid -> {"attrs": Dataset, "films": [uid], "assoc": id}
        self.films = {}      # uid -> {"attrs": Dataset, "session": uid, "boxes": [box dict]}
        self.boxes = {}      # uid -> box dict {"film": uid, "pos": int, "image": PIL, "mag": str}
        self.film_counter = 0

    # ---- helpers
    def _is_color(self, event):
        return event.context.abstract_syntax == BasicColorPrintManagementMeta

    @staticmethod
    def _copy(ds):
        out = Dataset()
        if ds is not None:
            for elem in ds:
                out.add(elem)
        return out

    # ---- N-CREATE
    def on_create(self, event):
        req = event.request
        cls = req.AffectedSOPClassUID
        uid = req.AffectedSOPInstanceUID or generate_uid()
        attrs = event.attribute_list
        rsp = self._copy(attrs)
        if req.AffectedSOPInstanceUID is None:
            rsp.AffectedSOPInstanceUID = uid

        with self.lock:
            if cls == BasicFilmSession:
                self.sessions[uid] = {"attrs": self._copy(attrs), "films": [], "assoc": id(event.assoc)}
                log.info("Film session created (copies=%s, medium=%s)",
                         attrs.get("NumberOfCopies", 1), attrs.get("MediumType", "-"))
                return 0x0000, rsp

            if cls == BasicFilmBox:
                fmt = str(attrs.get("ImageDisplayFormat", "STANDARD\\1,1"))
                n = count_boxes(fmt)
                session_uid = None
                if "ReferencedFilmSessionSequence" in attrs and attrs.ReferencedFilmSessionSequence:
                    session_uid = attrs.ReferencedFilmSessionSequence[0].ReferencedSOPInstanceUID
                if session_uid not in self.sessions:
                    # Be tolerant: attach to the latest session of this association
                    mine = [k for k, v in self.sessions.items() if v["assoc"] == id(event.assoc)]
                    session_uid = mine[-1] if mine else None
                box_cls = BasicColorImageBox if self._is_color(event) else BasicGrayscaleImageBox
                film = {"attrs": self._copy(attrs), "session": session_uid, "boxes": []}
                seq = []
                for pos in range(1, n + 1):
                    buid = generate_uid()
                    box = {"film": uid, "pos": pos, "image": None, "mag": None}
                    self.boxes[buid] = box
                    film["boxes"].append(box)
                    item = Dataset()
                    item.ReferencedSOPClassUID = box_cls
                    item.ReferencedSOPInstanceUID = buid
                    seq.append(item)
                self.films[uid] = film
                if session_uid:
                    self.sessions[session_uid]["films"].append(uid)
                rsp.ReferencedImageBoxSequence = seq
                log.info("Film box created: format=%s (%d images), orientation=%s, size=%s",
                         fmt, n, attrs.get("FilmOrientation", "-"), attrs.get("FilmSizeID", "-"))
                return 0x0000, rsp

        log.warning("N-CREATE for unsupported class %s", cls)
        return 0x0118, None

    # ---- N-SET
    def on_set(self, event):
        req = event.request
        cls, uid = req.RequestedSOPClassUID, req.RequestedSOPInstanceUID
        mods = event.modification_list
        with self.lock:
            if cls in (BasicGrayscaleImageBox, BasicColorImageBox):
                box = self.boxes.get(uid)
                if box is None:
                    return 0x0112, None  # No such SOP instance
                seq = mods.get("BasicGrayscaleImageSequence") or mods.get("BasicColorImageSequence")
                if "ImageBoxPosition" in mods:
                    box["pos"] = int(mods.ImageBoxPosition)
                if "MagnificationType" in mods and mods.MagnificationType:
                    box["mag"] = str(mods.MagnificationType)
                if seq:
                    try:
                        box["image"] = dataset_to_image(seq[0], mods.get("Polarity", "NORMAL"))
                    except Exception:
                        log.exception("Could not decode image for box %s", box["pos"])
                        return 0x0110, None
                return 0x0000, Dataset()
            if cls == BasicFilmBox and uid in self.films:
                for elem in mods:
                    self.films[uid]["attrs"].add(elem)
                return 0x0000, Dataset()
            if cls == BasicFilmSession and uid in self.sessions:
                for elem in mods:
                    self.sessions[uid]["attrs"].add(elem)
                return 0x0000, Dataset()
        return 0x0112, None

    # ---- N-ACTION (print)
    def on_action(self, event):
        req = event.request
        cls, uid = req.RequestedSOPClassUID, req.RequestedSOPInstanceUID
        with self.lock:
            if cls == BasicFilmBox and uid in self.films:
                film_uids = [uid]
                session = self.sessions.get(self.films[uid]["session"])
            elif cls == BasicFilmSession and uid in self.sessions:
                session = self.sessions[uid]
                film_uids = list(session["films"])
            else:
                return 0x0112, None
            copies = 1
            if session is not None:
                try:
                    copies = int(session["attrs"].get("NumberOfCopies", 1) or 1)
                except (TypeError, ValueError):
                    copies = 1
            copies = max(1, min(copies, self.cfg["printer"].getint("max_copies", 5)))
            for fuid in film_uids:
                film = self.films[fuid]
                filled = sum(1 for b in film["boxes"] if b["image"] is not None)
                self.film_counter += 1
                log.info("PRINT film #%d: %d/%d images, %d copies",
                         self.film_counter, filled, len(film["boxes"]), copies)
                snapshot = {"attrs": film["attrs"],
                            "boxes": [dict(b) for b in film["boxes"]]}
                self.worker.submit(snapshot, copies, self.film_counter)
        return 0x0000, None

    # ---- N-DELETE
    def on_delete(self, event):
        req = event.request
        cls, uid = req.RequestedSOPClassUID, req.RequestedSOPInstanceUID
        with self.lock:
            if cls == BasicFilmBox:
                self._drop_film(uid)
            elif cls == BasicFilmSession:
                sess = self.sessions.pop(uid, None)
                if sess:
                    for f in sess["films"]:
                        self._drop_film(f)
        return 0x0000

    def _drop_film(self, uid):
        film = self.films.pop(uid, None)
        if film:
            for k in [k for k, b in self.boxes.items() if b["film"] == uid]:
                del self.boxes[k]

    # ---- N-GET (printer status)
    def on_get(self, event):
        ds = Dataset()
        ds.PrinterStatus = "NORMAL"
        ds.PrinterStatusInfo = "NORMAL"
        ds.PrinterName = (self.cfg["printer"].get("name", "") or "Windows default printer")[:64]
        ds.Manufacturer = "KDC"
        ds.ManufacturerModelName = "KDC DICOM Print Server"
        ds.SoftwareVersions = VERSION
        return 0x0000, ds

    # ---- association bookkeeping
    def on_conn(self, event):
        a = event.assoc
        log.info("Association from %s (%s:%s) to AE %s",
                 a.requestor.ae_title, a.requestor.address, a.requestor.port, a.acceptor.ae_title)

    def on_close(self, event):
        with self.lock:
            for uid in [k for k, v in self.sessions.items() if v["assoc"] == id(event.assoc)]:
                for f in self.sessions[uid]["films"]:
                    self._drop_film(f)
                del self.sessions[uid]


def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    if sys.stdout is not None:  # no console when built as a windowed .exe
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)
    fh = RotatingFileHandler(os.path.join(APP_DIR, "print_server.log"),
                             maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)


def main():
    ap = argparse.ArgumentParser(description="KDC DICOM Print Server")
    ap.add_argument("--config", default=os.path.join(APP_DIR, "config.ini"))
    ap.add_argument("--no-print", action="store_true", help="save images only, do not print")
    args = ap.parse_args()

    setup_logging()
    cfg = load_config(args.config)
    ae_title = cfg["dicom"].get("ae_title", "EPSON_A3").strip()[:16]
    port = cfg["dicom"].getint("port", 104)

    worker = PrintWorker(cfg, do_print=not args.no_print)
    worker.cleanup_old()
    worker.start()
    scp = PrintSCP(cfg, worker)

    ae = AE(ae_title=ae_title)
    ae.require_called_aet = cfg["dicom"].getboolean("require_called_ae", False)
    ae.maximum_pdu_size = 0  # unlimited
    ae.dimse_timeout = 120
    ae.network_timeout = 120
    for cx in (BasicGrayscalePrintManagementMeta, BasicColorPrintManagementMeta,
               Printer, Verification):
        ae.add_supported_context(cx)

    handlers = [
        (evt.EVT_N_CREATE, scp.on_create),
        (evt.EVT_N_SET, scp.on_set),
        (evt.EVT_N_ACTION, scp.on_action),
        (evt.EVT_N_DELETE, scp.on_delete),
        (evt.EVT_N_GET, scp.on_get),
        (evt.EVT_ACCEPTED, scp.on_conn),
        (evt.EVT_RELEASED, scp.on_close),
        (evt.EVT_ABORTED, scp.on_close),
    ]
    log.info("KDC DICOM Print Server %s listening: AE=%s port=%d paper=%s printer=%s",
             VERSION, ae_title, port, cfg["printer"].get("paper", "A3"),
             cfg["printer"].get("name", "") or "(Windows default)")
    try:
        ae.start_server(("0.0.0.0", port), block=True, evt_handlers=handlers)
    except OSError as e:
        log.error("Cannot open port %d: %s (is another program using it, or run as admin?)", port, e)
        time.sleep(10)
        sys.exit(1)


if __name__ == "__main__":
    main()
