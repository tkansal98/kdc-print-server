# KDC DICOM Print Server

Turns a Windows PC and an ordinary printer (Epson EcoTank L18050, A3) into a
DICOM printer for the GE CT console. The CT sends a film; this program lays the
images out on a 13x17 in (or A3) sheet and prints it. It also keeps a PNG copy of every film.

No Python is needed on the print PC. GitHub builds a ready-to-run `.exe`.

## Get the exe

- **Latest build:** open the repo on GitHub > **Actions** > latest "Build Windows exe" run > download
  **KDC-DICOM-Print-Server-windows** under *Artifacts*.
- **Release:** every push to `main` publishes a new release. Download `KDC-DICOM-Print-Server-windows.zip` from **Releases** on the repo page.

The download contains `KDCPrintServer.exe`, `TestPrint.exe`, `config.ini`,
`install.bat`, `restart.bat`, `uninstall.bat` and this README.

## 1. Set up the print PC (one time)

1. Install the Epson L18050 driver and print a Windows test page.
   In the printer's preferences (Printing Preferences > Paper Size > **User-Defined**),
   create a size called `13x17` (width **329** mm, height 431.8 mm; the Epson's maximum width is 329 mm), save it and set it as default.
   Also choose your paper type (glossy/photo or film paper) with **High** quality.
   The server prints on 13x17 sheets by default; change `paper` in config.ini for A3 or other sizes.
2. Give the PC a **fixed IP address** on the same network as the CT, e.g. `192.168.1.50`.
3. Unzip the package to a permanent folder, e.g. `C:\KDC-Print`.
4. Edit `config.ini`:
   - `name =` the exact Windows printer name (e.g. `EPSON L18050 Series`), or leave it blank to use the default printer
   - `header_text =` the text printed at the top of each film
5. Right-click `install.bat` and choose **Run as administrator**. This opens the
   firewall, starts the server in the background, and makes it start at every login.
6. Run `TestPrint.exe`. A 13x17 sheet with 12 test circles should print.

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

### Choosing the paper for each print

For every print, a small window pops up on the print PC asking **which paper is loaded:
13 x 17 or A4**. The paper the scanner asked for is highlighted, and it is used
automatically after 20 seconds if nobody clicks. "Don't print" cancels the film.
Further films from the same scanner within 60 seconds use the same answer, so a CT
series asks only once. Settings: `ask_paper`, `paper_choices`, `ask_timeout` and
`ask_remember_seconds` in config.ini.

**Make the Epson really switch paper (recommended).** The Epson driver often ignores
paper sizes requested by programs. The reliable fix is to add the Epson to Windows twice:

1. Settings > Printers & scanners > **Add a printer** > "The printer that I want isn't listed"
   > **Add a local printer** > *Use an existing port*: choose the same port as the current
   Epson (e.g. `USB001`) > pick the **EPSON L18050** driver > **Use the driver that is
   currently installed** > name it `EPSON A4`.
2. Rename the original Epson to `EPSON 13x17` (or keep its name).
3. Open **Printing Preferences** of each one: set `EPSON A4` to **A4**, and `EPSON 13x17`
   to your 13x17 user-defined size (329 x 431.8 mm). Set paper type/quality on both.
4. In config.ini:
   ```
   [windows_printers]
   13X17 = EPSON 13x17
   A4 = EPSON A4
   ```
5. Run `restart.bat`.

Choosing A4 in the pop-up then prints through `EPSON A4` with its own A4 settings.

### 13x17 and A4 on the same printer

Add **two** printer entries on the CT, both with the same network address and port:

| DICOM printer label / AET | Prints on |
|---|---|
| `EPSON_13X17` | 13x17 in sheets |
| `EPSON_A4` | A4 |

Choose the printer that matches the paper loaded in the Epson. The mapping is in
`config.ini` under `[paper_by_ae]`. If the CT sends to some other AE title, the film size
chosen on the CT decides (`[paper_by_film_size]`), and otherwise `paper` is used.

## Troubleshooting

Everything is logged in `print_server.log` next to the exe.

- **CT can't connect.** Is the PC on and logged in? Check in Task Manager that
  `KDCPrintServer.exe` is running. Check the IP, and that install.bat was run as administrator.
- **"Association rejected".** The AET or port on the CT doesn't match config.ini.
- **The log shows "PRINT film" but nothing comes out.** Check the printer name in
  config.ini and look for jobs stuck in the Windows print queue.
- **Print comes out small / on the wrong size.** Look in `print_server.log` for the
  "Driver page: ... wanted ..." line. It shows the paper size the Epson driver actually used.
  The server always fills that page. If it says 210 x 297 mm (A4) while you wanted 13x17,
  select the 13x17 user-defined size in the Epson Printing Preferences.
- **Wrong paper size or orientation.** Set `paper` / `orientation` in config.ini.
  If the Epson ignores the 13x17 size, set `set_paper_size = no` so it uses the default
  size from the Epson printer preferences (set that to your 13x17 user-defined size).
- **To save ink,** set `background = white`. This only changes margins and gaps.
- **Port 104 is already in use** (e.g. by a PACS): set `port = 11112` in config.ini and on the
  CT, then run install.bat again.

Saved copies are in `prints\YYYY-MM-DD\` and are deleted after `keep_days`.
Paper prints are fine as patient copies but are not diagnostic-grade film.

## Developers: running from source

Requires Python 3.11+. Run `install_python.bat`, then `run_python.bat`.
`python kdc_print_server.py --no-print` saves films without printing.
`python test_send_film.py <ip> <port> <AE> "STANDARD\4,5"` sends a test film.
