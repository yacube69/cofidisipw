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

## D. On a Cofidis server

What a shared server in Cofidis' network needs beyond section A; the questions for Cofidis are in CIPW-61.

| Topic | Today | Planned |
| --- | --- | --- |
| Language model of the AI report | Mistral: its API with `MISTRAL_API_KEY`, or a Mistral model on Cofidis' server (`llm.url`) | `docs/llm.md`, CIPW-58 |
| Sign-in and roles | none | local accounts, Active Directory or SSO, Cofidis chooses: `docs/users.md`, CIPW-59 |
| Results and corrections | in memory, the last 20, lost on a restart | SQLite on a Docker volume: CIPW-60 |
| HTTPS | none in Fides | a reverse proxy with Cofidis' certificate in front of port 8000 |
| Internet | ARES and Mistral's API, if allowed | without it: the ARES snapshot of our dataset and a model on Cofidis' server |

## Using the app

- **Open a sample** in the sidebar on the left: it opens at once, without OCR. *New analysis* at the top of the sidebar brings back the upload form, and every file analysed in this session stays listed there.
- **Analyse a PDF:** drop it on *Drop a PDF or browse* (or click it), choose *Individual* or *Consolidated* statements and click *Analyse*.
  - A digital PDF takes seconds.
  - A scan is read by OCR: about half a minute on a 6-core computer, up to 4 minutes for a long annual report.
- **Statement pages:** Fides finds them automatically. When a long annual report gives few values, look up the pages of the balance sheet and the income statement in the PDF and enter them, e.g. `75-76` and `77`.
- **What Fides reads:** Czech statements in the form of Vyhláška 500/2002 Sb., full or abbreviated, digital or scanned. IFRS statements (large companies, many annual reports) and bank statements are not read yet; a note above the result says so.
- **The steps:**
  - **Overview** shows what needs attention.
  - **Extraction** puts every value next to its place on the page: grey confident, amber check, red review. Click a value to review: *Confirm value* when it is read right, or type the right amount and *Save correction*. The checks, ratios and the assessment are computed again at once (`docs/app.md`).
  - **Validation** lists the accounting checks.
  - **Ratios** shows the ratios with their traffic lights and the bankruptcy models IN05 and Altman Z′.
  - **AI report** shows the assessment. Until the AI report (CIPW-16) is built, and whenever no AI model is connected, fixed rules build it from the numbers, and a banner says so (`docs/report.md`). No API key is needed.
- A *Mock data* badge marks a step that is not built yet and shows mock data.
- **Results stay in memory:** the app keeps the last 20, and they are gone after a restart. In the header of a result, *Export PDF* downloads the report in Cofidis' layout and *Export JSON* downloads the full result.

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
| An annual report gives few or no values | Read the notes above the result. For IFRS or bank statements, Fides does not read them yet. Otherwise enter the statement pages (see *Using the app*). |
| A scan takes very long | OCR uses every processor core: close other heavy programs. On a Mac with an Apple chip the Docker image runs emulated (see A); run it from the repository (B) instead. |
