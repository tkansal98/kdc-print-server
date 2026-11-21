# KDC DICOM Print Server

Turns a Windows PC and an ordinary printer (Epson EcoTank L18050, A3) into a
DICOM printer for the GE CT console. The CT sends a film; this program lays the
images out on an A3 page and prints it. It also keeps a PNG copy of every film.

No Python is needed on the print PC. GitHub builds a ready-to-run `.exe`.

## Get the exe

- **Latest build:** open the repo on GitHub > **Actions** > latest "Build Windows exe" run > download
  **KDC-DICOM-Print-Server-windows** under *Artifacts*.
- **Release:** push a tag such as `v1.0`, and a zip appears under **Releases**.

The download contains `KDCPrintServer.exe`, `TestPrint.exe`, `config.ini`,
`install.bat`, `restart.bat`, `uninstall.bat` and this README.

## 1. Set up the print PC (one time)

1. Install the Epson L18050 driver and print a Windows test page.
   In the printer's preferences, set the paper to **A3** and choose your paper type
   (glossy/photo or film paper) with **High** quality.
2. Give the PC a **fixed IP address** on the same network as the CT, e.g. `192.168.1.50`.
3. Unzip the package to a permanent folder, e.g. `C:\KDC-Print`.
4. Edit `config.ini`:
   - `name =` the exact Windows printer name (e.g. `EPSON L18050 Series`), or leave it blank to use the default printer
   - `header_text =` the text printed at the top of each film
5. Right-click `install.bat` and choose **Run as administrator**. This opens the
   firewall, starts the server in the background, and makes it start at every login.
6. Run `TestPrint.exe`. An A3 page with 12 test circles should print.

After changing `config.ini`, run `restart.bat`.
To remove the server, run `uninstall.bat` as administrator.

The server starts when Windows logs in. Set the PC to log in automatically, or
leave it logged in.

## 2. Add the printer on the GE CT

In the CT's DICOM printer list, choose Add and open **Define new printer parameters**:

| Field | Value |
|---|---|
| DICOM printer label | `EPSON_A3` (any name) |
| Host name | the PC's name, e.g. `PRINTPC` |
| Application Entity Title (AET) | `EPSON_A3`, exactly as in config.ini |
| Network address | the PC's IP, e.g. `192.168.1.50` |
| Port number | `104` |
| Layouts | tick the ones you use |
| 12 bits image supported | tick it |
| Colour supported | leave unticked |
| Film size | tick **14 in x 17 in** and **A3**. For W/H, copy the values from the DRYPIXPRIMA entry |
| Filming mode | Printer pixel size |
| Magnification type | Cubic |

Save the entry, then print one film from the CT.

## Troubleshooting

Everything is logged in `print_server.log` next to the exe.

- **CT can't connect.** Is the PC on and logged in? Check in Task Manager that
  `KDCPrintServer.exe` is running. Check the IP, and that install.bat was run as administrator.
- **"Association rejected".** The AET or port on the CT doesn't match config.ini.
- **The log shows "PRINT film" but nothing comes out.** Check the printer name in
  config.ini and look for jobs stuck in the Windows print queue.
- **Wrong paper size or orientation.** Set `paper` / `orientation` in config.ini.
- **To save ink,** set `background = white`. This only changes margins and gaps.
- **Port 104 is already in use** (e.g. by a PACS): set `port = 11112` in config.ini and on the
  CT, then run install.bat again.

Saved copies are in `prints\YYYY-MM-DD\` and are deleted after `keep_days`.
Paper prints are fine as patient copies but are not diagnostic-grade film.

## Developers: running from source

Requires Python 3.11+. Run `install_python.bat`, then `run_python.bat`.
`python kdc_print_server.py --no-print` saves films without printing.
`python test_send_film.py <ip> <port> <AE> "STANDARD\4,5"` sends a test film.
