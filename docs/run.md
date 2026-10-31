# Running Fides on your computer

A step-by-step guide for the team: how to start the web app, open it in the browser and show it to others. Everything runs on your own computer; the PDFs you upload never leave it.

| Way | For | You need | First start |
| --- | --- | --- | --- |
| [A. Docker image](#a-docker-image) | trying the app, demos, testing | Docker Desktop | ~10 min (430 MB download) |
| [B. From the repository](#b-from-the-repository) | changing the code | Git, Python 3.11+, Tesseract, Node.js 22 | ~20 min |
| [C. Show it to others](#c-show-it-to-others-on-your-network) | a colleague on the same Wi-Fi | A or B running | 2 min |

Not sure which one? Take **A**: nothing but Docker has to be installed, and the image is the same one Cofidis gets.

## A. Docker image

1. **Install Docker.**
   - Windows and macOS: [Docker Desktop](https://www.docker.com/products/docker-desktop/). Windows asks for WSL 2 during the installation; accept it and restart the computer.
   - Linux: [Docker Engine](https://docs.docker.com/engine/install/).
2. **Start Docker Desktop** and wait until it shows *Engine running*.
3. **Start Fides** in a terminal (PowerShell, Terminal, any shell):

   ```bash
   docker run --rm -p 8000:8000 ghcr.io/yacube69/cofidisipw:main
   ```

   The first start downloads the image (about 430 MB). The app is ready when the log shows `Application startup complete`.
4. **Open http://localhost:8000** in the browser.
5. **Stop it** with `Ctrl+C` in the terminal.

**Update** to the latest `main` (every merge to `main` publishes a new image):

```bash
docker pull ghcr.io/yacube69/cofidisipw:main
```

**With the repository cloned**, `docker compose up` does the same with the settings of `docker-compose.yml`: it keeps the run logs in `outputs/` and restarts the app with Docker. Stop it with `docker compose down`.

The image is built for Intel/AMD processors. On a Mac with an Apple chip Docker Desktop runs it through emulation: it works, but OCR of a scan is slower.

## B. From the repository

For developers: you run the code of your branch, and changes show without building an image.

1. **Get the code.**

   ```bash
   git clone https://github.com/yacube69/cofidisipw.git
   cd cofidisipw
   ```

2. **Install Tesseract 5.** Only the program is needed: the Czech model ships with Fides (`models/tessdata`).

   | OS | Install |
   | --- | --- |
   | Windows | [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) or `winget install UB-Mannheim.TesseractOCR` |
   | macOS | `brew install tesseract` |
   | Linux | `sudo apt install tesseract-ocr` |

3. **Install Python 3.11 or newer** ([python.org](https://www.python.org/downloads/)), then the packages:

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. **Create `.env`** from the example:

   ```bash
   cp .env.example .env             # Windows: copy .env.example .env
   ```

   On Windows, set the path to Tesseract in it (the installer does not put it on `PATH`):

   ```
   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```

   Check that OCR works: `python scripts/hello_ocr.py` ends with `OK`.

5. **Build the web app once.** You need [Node.js](https://nodejs.org/) 22 LTS or newer.

   ```bash
   cd web
   npm ci
   npm run build
   cd ..
   ```

6. **Start Fides** and open **http://localhost:8000**:

   ```bash
   uvicorn src.api:app --port 8000
   ```

   Stop it with `Ctrl+C`.

**After `git pull`:** run `pip install -r requirements.txt` again, and `npm run build` in `web/` when something in `web/` changed. Otherwise `localhost:8000` shows the old screens.

**Working on the screens:** run the API and the Vite dev server side by side in two terminals, then open **http://localhost:5173**. Every saved change in `web/src` shows at once.

```bash
uvicorn src.api:app --reload --port 8000     # terminal 1, repository folder
npm run dev                                  # terminal 2, in web/
```

## C. Show it to others on your network

A colleague on the same Wi-Fi or office network can open the app running on your computer.

1. Start Fides so that it listens on the network:
   - Docker: the `docker run` command from A already does.
   - `docker compose`: in `docker-compose.yml` change `"127.0.0.1:8000:8000"` to `"8000:8000"`.
   - From the repository: `uvicorn src.api:app --host 0.0.0.0 --port 8000`.
2. Find your IP address:

   | OS | Command | Look for |
   | --- | --- | --- |
   | Windows | `ipconfig` | IPv4 Address, e.g. `192.168.1.23` |
   | macOS | `ipconfig getifaddr en0` | the address it prints |
   | Linux | `hostname -I` | the first address |

3. Your colleague opens **http://192.168.1.23:8000**, with your address instead of the example.
4. If Windows asks whether to allow access, allow it for **private networks** only.

**Careful:** the app has no sign-in. Everyone who can open it can upload PDFs and open the last 20 results, including yours. Share it only on a network you trust, never on public Wi-Fi with real statements, and stop it when you are done.

## Using the app

- **Open a sample** in the sidebar: it opens at once, without OCR.
- **Analyse a PDF:** drop it on *Drop a PDF or browse* (or click it), choose *Individual* or *Consolidated* statements and click *Analyse*.
  - A digital PDF takes seconds.
  - A scan is read by OCR: about half a minute on a 6-core computer, up to 4 minutes for a long annual report.
- **The steps:**
  - **Overview** shows what needs attention.
  - **Extraction** puts every value next to its place on the page: green confident, yellow check, red review.
  - **Validation** lists the accounting checks.
  - **Ratios** shows the ratios and the bankruptcy models IN05 and Altman Z′.
  - **AI report** is still mock data.
- A *Mock data* badge marks the steps that are not built yet. They are the benchmarks and the AI report, so no API key is needed for now.
- **Results stay in memory:** the app keeps the last 20, and they are gone after a restart. *Export result (JSON)* downloads the full result.

More about the screens and the API: [app.md](app.md). All settings: [settings.md](settings.md).

## When something goes wrong

| Problem | Fix |
| --- | --- |
| `port is already allocated` or `address already in use` | Another program uses port 8000. Use another port, e.g. `docker run --rm -p 8080:8000 …` or `uvicorn src.api:app --port 8080`, and open http://localhost:8080. |
| `Cannot connect to the Docker daemon` / `docker: command not found` | Docker Desktop is not running or not installed (step A.1–2). |
| Docker Desktop: *WSL 2 installation is incomplete* | Run `wsl --update` in PowerShell, then restart Docker Desktop. |
| `TesseractNotFoundError` | Tesseract is not installed, or on Windows `TESSERACT_CMD` in `.env` points to the wrong place (step B.4). |
| `localhost:8000` shows `{"detail":"Not Found"}` | The web app is not built: run step B.5. |
| The screens look old after `git pull` | Rebuild the web app (step B.5), then reload the page with `Ctrl+F5`. |
| `npm ci` fails | Node.js is too old: install 22 LTS or newer. |
| A scan takes very long | OCR uses every processor core: close other heavy programs. On a Mac with an Apple chip the Docker image runs emulated (see A); run it from the repository (B) instead. |
