"""``cli.py init`` setup wizard (``cva/setup``): ``wizard.*``.

The wizard asks the bot language first and speaks it from then on
(``auto`` resolves to the system language). Machine answers (numbers,
provider names, ``y``/``n``) work in both languages; Spanish also accepts
``s``/``sí``.
"""

from typing import Final

EN: Final[dict[str, str]] = {
    # Before the language is known: both languages on one line.
    "wizard.lang_prompt": "  Language / Idioma ({choices}) [{default}]: ",
    "wizard.lang_retry": "  Please choose one of / Elige una de: {choices}.",
    "wizard.lang_intro": (
        "lazyhunt setup / Configuración de lazyhunt. "
        "en = English, es = español, auto = your system language / el idioma "
        "del sistema."
    ),
    "wizard.intro1": "lazyhunt setup. Every step can be skipped with Enter, and nothing is",
    "wizard.intro2": "written until you confirm the summary at the end. Safe to re-run.",
    "wizard.step": "Step {n}/{total} - {title}",
    "wizard.title.language": "Bot language",
    "wizard.title.prereq": "Prerequisites",
    "wizard.title.provider": "AI provider",
    "wizard.title.telegram": "Telegram",
    "wizard.title.bridge": "Extension bridge secret",
    "wizard.title.profile": "Profile directory",
    "wizard.title.gmail": "Gmail (optional, off by default)",
    "wizard.yes_no": "[y/N]",
    "wizard.replace": "  {key} is already set in {where}{shown}. Replace it?",
    "wizard.current_value": " (current value: {value})",
    "wizard.keeping": "  Keeping the existing {key}.",
    # --- prerequisites -------------------------------------------------------
    "wizard.python_ok": "  Python {version}: OK",
    "wizard.python_old": "  Python {version}: lazyhunt needs Python 3.12 or newer.",
    "wizard.uv_missing": (
        "  uv was not found on PATH: install it from https://docs.astral.sh/uv/"
    ),
    "wizard.codex_not_installed": (
        "  Codex CLI: not installed (optional provider codex_cli)."
    ),
    "wizard.codex_missing": (
        "  `codex` was not found on PATH. To use the codex_cli provider, "
        "install the Codex CLI (https://developers.openai.com/codex/cli) "
        "and run `codex login`, or choose another provider."
    ),
    "wizard.codex_version": "  Codex CLI: {line}",
    "wizard.codex_no_version": (
        "  Codex CLI: found, but `codex --version` did not answer."
    ),
    "wizard.codex_logged_in": "  Codex CLI: logged in.",
    "wizard.codex_logged_out": (
        "  Codex CLI is installed but not logged in: run `codex login` "
        "with your ChatGPT account, then re-run this wizard if you like."
    ),
    "wizard.codex_unknown": (
        "  Could not tell whether Codex CLI is logged in; check it with "
        "`codex login status`."
    ),
    "wizard.claude_missing": (
        "  `claude` was not found on PATH. The default AI provider is your own "
        "Claude Code CLI: install it from "
        "https://docs.anthropic.com/en/docs/claude-code and log in, or choose "
        "another provider in the next step. Continuing."
    ),
    "wizard.claude_version": "  Claude Code: {line}",
    "wizard.claude_no_version": (
        "  Claude Code: found, but `claude --version` did not answer."
    ),
    "wizard.claude_logged_in": "  Claude Code: logged in.",
    "wizard.claude_logged_out": (
        "  Claude Code is installed but not logged in: run `claude auth login` "
        "(or start `claude` and use /login), then re-run this wizard if you like."
    ),
    "wizard.claude_unknown": (
        "  Could not tell whether Claude Code is logged in; check it with "
        "`claude auth status`."
    ),
    # --- language --------------------------------------------------------------
    "wizard.lang_explain": (
        "  The language of the Telegram bot, the command line and the dashboard. "
        "CVs and cover letters keep following each offer's language. "
        "auto = your system language; choose en or es to force one."
    ),
    # --- provider --------------------------------------------------------------
    "wizard.provider_intro": (
        "  Which AI writes and judges for you (details: docs/providers.md):"
    ),
    "wizard.provider.claude_code": (
        "your own installed and logged-in Claude Code CLI; no API key (default)"
    ),
    "wizard.provider.codex_cli": (
        "your own installed and logged-in OpenAI Codex CLI; no API key"
    ),
    "wizard.provider.anthropic": "the Anthropic API with your API key",
    "wizard.provider.openai_compatible": (
        "any OpenAI-compatible API: OpenAI, Gemini, OpenRouter, Groq, Mistral, "
        "or a local model with Ollama or LM Studio"
    ),
    "wizard.provider_web": (
        "  Web research of company facts works only with claude_code and "
        "codex_cli: API models have no browsing tool."
    ),
    "wizard.provider_prompt": "  Provider [{default}]: ",
    "wizard.provider_retry": "  Please choose one of: {choices} (or 1-4).",
    "wizard.claude_code_note": (
        "  Uses your Claude Code login (`claude auth login`); no key needed."
    ),
    "wizard.codex_note": (
        "  Uses your Codex CLI login (run `codex login` once); no key needed. "
        "Set codex_cli_path in config.local.toml if `codex` is not on PATH."
    ),
    "wizard.endpoint_intro": "  OpenAI-compatible endpoint:",
    "wizard.preset_other": "Other (type the base URL)",
    "wizard.endpoint_prompt": "  Endpoint [{default}]: ",
    "wizard.endpoint_retry": "  Please answer a number from 1 to {count}.",
    "wizard.base_url_prompt": "  Base URL{shown}: ",
    "wizard.base_url_retry": (
        "  Please type the full base URL, starting with http:// or https:// "
        "(for example https://api.example.com/v1)."
    ),
    "wizard.local_model_hint": (
        "  Type the name of a model you have downloaded "
        "(for Ollama, see `ollama list`)."
    ),
    "wizard.model_prompt": "  Model{shown}: ",
    "wizard.model_retry": "  Please type a model name.",
    "wizard.local_key_note": (
        "  A local server ignores the API key, but one must be set: any "
        "non-empty value works. Press Enter to use the placeholder {placeholder}."
    ),
    "wizard.hint_keep_key": "Enter to keep the current key",
    "wizard.hint_placeholder": "Enter for the placeholder",
    "wizard.hint_skip": "Enter to skip",
    "wizard.api_key_prompt": "  API key, stored as {key} in .env (hidden; {hint}): ",
    "wizard.key_kept": "  Keeping the {key} already in .env.",
    "wizard.key_skipped": (
        "  Skipped: add {key}=<your key> to .env before starting the bot."
    ),
    # --- telegram ----------------------------------------------------------------
    "wizard.botfather": (
        "  Create your Telegram bot first (2 minutes, in the Telegram app):\n"
        "    1. Search for @BotFather (blue check mark) and open it.\n"
        "    2. Send /newbot.\n"
        "    3. Type a name for your bot (any name, e.g. My job hunter).\n"
        '    4. Type a username that ends in "bot" (e.g. myjobhunter_bot).\n'
        "    5. BotFather replies with a token like 1234567890:AAH...: copy the "
        "whole token.\n"
        "  Already have a bot? @BotFather → /mybots → your bot → API Token.\n"
        "  Paste it below. It stays hidden while you paste (nothing appears): "
        "that is normal. Keep it private."
    ),
    "wizard.token_prompt": "  Bot token (hidden; Enter to skip): ",
    "wizard.token_saved_invalid": (
        "  The token saved in .env does not look like a Telegram bot token "
        "(was another key pasted?). Paste the @BotFather token to continue."
    ),
    "wizard.token_invalid": (
        "  This does not look like a Telegram bot token: copy it from "
        "@BotFather (/mybots → your bot → API Token); it looks like "
        "1234567890:AA…"
    ),
    "wizard.token_is_ai_key": (
        "  That is your AI API key, not the Telegram bot token. Copy the "
        "token from @BotFather (/mybots → your bot → API Token)."
    ),
    "wizard.token_saved": "  Bot token {tail} saved.",
    "wizard.token_kept": "  Keeping the token already in .env.",
    "wizard.token_skipped": "  Skipped: set {key} in .env before starting the bot.",
    "wizard.owner_claim": (
        "  Your Telegram account: no number to look up. When the bot starts it "
        "prints a one-time code; send /start <code> to your bot and you become "
        "its owner (the bot answers only you)."
    ),
    "wizard.owner_kept": ("  Owner: already set in config.local.toml ({owner}); kept."),
    # --- bridge ----------------------------------------------------------------------
    "wizard.bridge_intro": (
        "  The browser extension sends offers to the bot over 127.0.0.1 with a "
        "shared secret. You will not need to copy it: the bot gives the "
        "extension a one-time pairing code (/connect)."
    ),
    "wizard.bridge_replace": (
        "  A bridge secret is already set in .env. Replace it with a new "
        "random one (the extension must then be paired again)?"
    ),
    "wizard.bridge_kept": "  Keeping the existing bridge secret.",
    "wizard.bridge_generated": (
        "  Generated a new random secret; it will be stored in .env as {key}."
    ),
    # --- profile -----------------------------------------------------------------------
    "wizard.profile_intro": (
        "  Everything lazyhunt knows about you (claims, base CVs, voice, "
        "preferences) lives in one directory. It holds personal data: keep it out "
        "of version control (profiles/* is already gitignored, except the demo)."
    ),
    "wizard.profile_prompt": "  Profile directory [{default}]: ",
    "wizard.profile_retry": (
        "  Choose a directory inside this directory and outside profiles/demo "
        "(for example profiles/me). To keep your profile somewhere else, set "
        "profile_dir in config.local.toml by hand."
    ),
    "wizard.profile_not_dir": "  {path} exists and is not a directory; skipped.",
    "wizard.profile_exists": (
        "  {path} already exists; it is used as is and never modified."
    ),
    "wizard.profile_no_demo": (
        "  The demo profile was not found at {demo}; create {path} yourself "
        "(see profiles/demo in the repository for the layout)."
    ),
    "wizard.kind_skeleton": (
        "  [1] a skeleton with the demo's layout and clearly marked placeholders"
    ),
    "wizard.kind_demo": (
        "  [2] a copy of the fictional demo profile, to try lazyhunt first"
    ),
    "wizard.kind_empty": (
        "  [3] an empty profile: start the bot and answer its guided /setup "
        "in Telegram (recommended)"
    ),
    "wizard.kind_prompt": "  Start from [3]: ",
    "wizard.kind_retry": "  Please answer 1, 2 or 3.",
    "wizard.kind.empty": "empty",
    "wizard.kind.skeleton": "skeleton",
    "wizard.kind.demo": "demo",
    # --- gmail --------------------------------------------------------------------------
    "wizard.gmail": (
        "  Gmail sync is read-only and uses an OAuth client you create in your own "
        "Google Cloud project. To enable it later, follow docs/gmail.md, run "
        "`uv run cli.py gmail-auth` and set gmail_enabled = true in "
        "config.local.toml. Nothing is configured now."
    ),
    # --- summary --------------------------------------------------------------------------
    "wizard.planned": "Planned changes:",
    "wizard.env_set": "  {file}: set {key} (value hidden)",
    "wizard.env_replace": "  {file}: replace {key} (value hidden)",
    "wizard.env_missing": "  {file}: {key} not set (the AI provider needs it)",
    "wizard.create_profile": "  create {path}/ ({kind})",
    "wizard.nothing_to_change": (
        "Nothing to change: your configuration already matches these answers."
    ),
    "wizard.write_q": "Write these changes?",
    "wizard.nothing_written": "Nothing was written.",
    "wizard.saved": (
        "Saved. Secrets went to .env only (mode 600), settings to config.local.toml."
    ),
    "wizard.cancelled": "Setup cancelled. Nothing was written.",
    "wizard.invalid_toml": (
        "{file} is not valid TOML ({error}); fix it and run init again."
    ),
    # --- next steps --------------------------------------------------------------------------
    "wizard.next": "Next steps:",
    "wizard.next_start_claim": (
        "  1. Start the bot with `uv run bot.py`. It prints a one-time code: "
        "send /start <code> to your bot in Telegram to become its owner."
    ),
    "wizard.next_start": (
        "  1. Start the bot with `uv run bot.py` and send /help to it in Telegram."
    ),
    "wizard.next_extension": (
        "  2. Load the extension: open chrome://extensions, turn on Developer mode, "
        "click Load unpacked and select the extension/ directory."
    ),
    "wizard.next_pair": (
        "  3. Connect it: send {connect} to the bot, then in the extension's "
        "Options › Schedule and bot type the code under Pair with code (bridge "
        "URL http://127.0.0.1:{port}/bridge) and press Pair."
    ),
    "wizard.next_portals": (
        "  4. In Options > Portals enable only the portals whose terms you have read "
        "and accept; each one shows its terms warning and asks the browser for "
        "permission to that single domain. Some portals (notably LinkedIn) prohibit "
        "automated access and your account could be restricted."
    ),
    "wizard.next_profile_empty": (
        "  5. Your profile is empty: send {setup} to the bot in Telegram to set "
        "up your profile, then in Options › Searches press Suggest from my profile."
    ),
    "wizard.next_profile_fill": "  5. Fill in your profile in {path} (see its README.md).",
    "wizard.next_profile_create": (
        "  5. Create your profile directory and set profile_dir "
        "(see docs/getting-started.md)."
    ),
    "wizard.next_guide": "  Full guide: docs/getting-started.md",
    "wizard.show_secret": (
        "Show the bridge secret now, to paste it into the extension by hand "
        "instead of pairing?"
    ),
    # --- cli -----------------------------------------------------------------------------------
    "wizard.cli.owner_id": (
        "your numeric Telegram user id, if you already know it (optional: the bot "
        "can pair with you through a one-time code instead)"
    ),
    "wizard.cli.owner_id_invalid": "--owner-id must be a positive number",
}

ES: Final[dict[str, str]] = {
    "wizard.lang_prompt": "  Language / Idioma ({choices}) [{default}]: ",
    "wizard.lang_retry": "  Please choose one of / Elige una de: {choices}.",
    "wizard.lang_intro": (
        "lazyhunt setup / Configuración de lazyhunt. "
        "en = English, es = español, auto = your system language / el idioma "
        "del sistema."
    ),
    "wizard.intro1": (
        "Configuración de lazyhunt. Cada paso se puede saltar con Intro y no se"
    ),
    "wizard.intro2": (
        "escribe nada hasta que confirmes el resumen final. Puedes repetirla."
    ),
    "wizard.step": "Paso {n}/{total} - {title}",
    "wizard.title.language": "Idioma del bot",
    "wizard.title.prereq": "Requisitos",
    "wizard.title.provider": "Proveedor de IA",
    "wizard.title.telegram": "Telegram",
    "wizard.title.bridge": "Secreto del puente de la extensión",
    "wizard.title.profile": "Carpeta del perfil",
    "wizard.title.gmail": "Gmail (opcional, desactivado por defecto)",
    "wizard.yes_no": "[s/N]",
    "wizard.replace": "  {key} ya está definido en {where}{shown}. ¿Reemplazarlo?",
    "wizard.current_value": " (valor actual: {value})",
    "wizard.keeping": "  Se mantiene el {key} actual.",
    "wizard.python_ok": "  Python {version}: OK",
    "wizard.python_old": (
        "  Python {version}: lazyhunt necesita Python 3.12 o posterior."
    ),
    "wizard.uv_missing": (
        "  No se encontró uv en el PATH: instálalo desde https://docs.astral.sh/uv/"
    ),
    "wizard.codex_not_installed": (
        "  Codex CLI: no instalado (proveedor opcional codex_cli)."
    ),
    "wizard.codex_missing": (
        "  No se encontró `codex` en el PATH. Para usar el proveedor codex_cli, "
        "instala Codex CLI (https://developers.openai.com/codex/cli) y ejecuta "
        "`codex login`, o elige otro proveedor."
    ),
    "wizard.codex_version": "  Codex CLI: {line}",
    "wizard.codex_no_version": (
        "  Codex CLI: encontrado, pero `codex --version` no respondió."
    ),
    "wizard.codex_logged_in": "  Codex CLI: sesión iniciada.",
    "wizard.codex_logged_out": (
        "  Codex CLI está instalado pero sin sesión: ejecuta `codex login` con "
        "tu cuenta de ChatGPT y, si quieres, vuelve a ejecutar este asistente."
    ),
    "wizard.codex_unknown": (
        "  No se pudo saber si Codex CLI tiene la sesión iniciada; compruébalo "
        "con `codex login status`."
    ),
    "wizard.claude_missing": (
        "  No se encontró `claude` en el PATH. El proveedor de IA por defecto es "
        "tu propio Claude Code CLI: instálalo desde "
        "https://docs.anthropic.com/en/docs/claude-code e inicia sesión, o elige "
        "otro proveedor en el siguiente paso. Continuamos."
    ),
    "wizard.claude_version": "  Claude Code: {line}",
    "wizard.claude_no_version": (
        "  Claude Code: encontrado, pero `claude --version` no respondió."
    ),
    "wizard.claude_logged_in": "  Claude Code: sesión iniciada.",
    "wizard.claude_logged_out": (
        "  Claude Code está instalado pero sin sesión: ejecuta `claude auth login` "
        "(o abre `claude` y usa /login) y, si quieres, vuelve a ejecutar este "
        "asistente."
    ),
    "wizard.claude_unknown": (
        "  No se pudo saber si Claude Code tiene la sesión iniciada; compruébalo "
        "con `claude auth status`."
    ),
    "wizard.lang_explain": (
        "  El idioma del bot de Telegram, de la línea de comandos y del panel. "
        "Los CV y las cartas siguen el idioma de cada oferta. "
        "auto = el idioma del sistema; elige en o es para fijar uno."
    ),
    "wizard.provider_intro": (
        "  Qué IA escribe y evalúa por ti (detalles: docs/providers.md):"
    ),
    "wizard.provider.claude_code": (
        "tu propio Claude Code CLI instalado y con sesión; sin API key (por defecto)"
    ),
    "wizard.provider.codex_cli": (
        "tu propio OpenAI Codex CLI instalado y con sesión; sin API key"
    ),
    "wizard.provider.anthropic": "la API de Anthropic con tu API key",
    "wizard.provider.openai_compatible": (
        "cualquier API compatible con OpenAI: OpenAI, Gemini, OpenRouter, Groq, "
        "Mistral, o un modelo local con Ollama o LM Studio"
    ),
    "wizard.provider_web": (
        "  La búsqueda web de datos de empresas solo funciona con claude_code y "
        "codex_cli: los modelos por API no navegan."
    ),
    "wizard.provider_prompt": "  Proveedor [{default}]: ",
    "wizard.provider_retry": "  Elige uno de: {choices} (o 1-4).",
    "wizard.claude_code_note": (
        "  Usa tu sesión de Claude Code (`claude auth login`); no hace falta key."
    ),
    "wizard.codex_note": (
        "  Usa tu sesión de Codex CLI (ejecuta `codex login` una vez); no hace "
        "falta key. Define codex_cli_path en config.local.toml si `codex` no "
        "está en el PATH."
    ),
    "wizard.endpoint_intro": "  Endpoint compatible con OpenAI:",
    "wizard.preset_other": "Otro (escribe la URL base)",
    "wizard.endpoint_prompt": "  Endpoint [{default}]: ",
    "wizard.endpoint_retry": "  Responde con un número del 1 al {count}.",
    "wizard.base_url_prompt": "  URL base{shown}: ",
    "wizard.base_url_retry": (
        "  Escribe la URL base completa, empezando por http:// o https:// "
        "(por ejemplo https://api.example.com/v1)."
    ),
    "wizard.local_model_hint": (
        "  Escribe el nombre de un modelo que tengas descargado "
        "(en Ollama, mira `ollama list`)."
    ),
    "wizard.model_prompt": "  Modelo{shown}: ",
    "wizard.model_retry": "  Escribe el nombre de un modelo.",
    "wizard.local_key_note": (
        "  Un servidor local ignora la API key, pero hay que poner una: sirve "
        "cualquier valor no vacío. Pulsa Intro para usar el valor {placeholder}."
    ),
    "wizard.hint_keep_key": "Intro para mantener la key actual",
    "wizard.hint_placeholder": "Intro para el valor por defecto",
    "wizard.hint_skip": "Intro para saltar",
    "wizard.api_key_prompt": (
        "  API key, se guarda como {key} en .env (oculta; {hint}): "
    ),
    "wizard.key_kept": "  Se mantiene la {key} que ya hay en .env.",
    "wizard.key_skipped": (
        "  Saltado: añade {key}=<tu key> a .env antes de arrancar el bot."
    ),
    "wizard.botfather": (
        "  Primero crea tu bot de Telegram (2 minutos, en la app de Telegram):\n"
        "    1. Busca @BotFather (con la marca azul) y ábrelo.\n"
        "    2. Envía /newbot.\n"
        "    3. Escribe un nombre para tu bot (el que quieras, p. ej. Mi buscador).\n"
        '    4. Escribe un nombre de usuario que acabe en "bot" '
        "(p. ej. mibuscador_bot).\n"
        "    5. BotFather responde con un token como 1234567890:AAH...: copia el "
        "token entero.\n"
        "  ¿Ya tienes un bot? @BotFather → /mybots → tu bot → API Token.\n"
        "  Pégalo abajo. No se ve mientras lo pegas (no aparece nada): es "
        "normal. No lo compartas."
    ),
    "wizard.token_prompt": "  Token del bot (oculto; Intro para saltar): ",
    "wizard.token_saved_invalid": (
        "  El token guardado en .env no parece un token de bot de Telegram "
        "(¿se pegó otra clave?). Pega el token de @BotFather para seguir."
    ),
    "wizard.token_invalid": (
        "  Esto no parece un token de bot de Telegram: cópialo de @BotFather "
        "(/mybots → tu bot → API Token); tiene esta forma: 1234567890:AA…"
    ),
    "wizard.token_is_ai_key": (
        "  Esa es tu API key de IA, no el token del bot de Telegram. Copia el "
        "token de @BotFather (/mybots → tu bot → API Token)."
    ),
    "wizard.token_saved": "  Token del bot {tail} guardado.",
    "wizard.token_kept": "  Se mantiene el token que ya hay en .env.",
    "wizard.token_skipped": (
        "  Saltado: define {key} en .env antes de arrancar el bot."
    ),
    "wizard.owner_claim": (
        "  Tu cuenta de Telegram: no hace falta buscar ningún número. Al "
        "arrancar, el bot muestra un código de un solo uso; envía /start "
        "<código> a tu bot y pasarás a ser su dueño (el bot solo te responde a ti)."
    ),
    "wizard.owner_kept": (
        "  Dueño: ya definido en config.local.toml ({owner}); se mantiene."
    ),
    "wizard.bridge_intro": (
        "  La extensión del navegador envía ofertas al bot por 127.0.0.1 con un "
        "secreto compartido. No tendrás que copiarlo: el bot le da a la "
        "extensión un código de emparejamiento de un solo uso (/conectar)."
    ),
    "wizard.bridge_replace": (
        "  Ya hay un secreto del puente en .env. ¿Reemplazarlo por uno nuevo "
        "aleatorio (habrá que volver a emparejar la extensión)?"
    ),
    "wizard.bridge_kept": "  Se mantiene el secreto del puente actual.",
    "wizard.bridge_generated": (
        "  Generado un secreto aleatorio nuevo; se guardará en .env como {key}."
    ),
    "wizard.profile_intro": (
        "  Todo lo que lazyhunt sabe de ti (datos verificados, CV base, tu voz, "
        "preferencias) vive en una carpeta. Contiene datos personales: déjala "
        "fuera del control de versiones (profiles/* ya está en .gitignore, "
        "salvo la demo)."
    ),
    "wizard.profile_prompt": "  Carpeta del perfil [{default}]: ",
    "wizard.profile_retry": (
        "  Elige una carpeta dentro de esta y fuera de profiles/demo (por ejemplo "
        "profiles/me). Para guardar tu perfil en otro sitio, define profile_dir "
        "en config.local.toml a mano."
    ),
    "wizard.profile_not_dir": "  {path} existe y no es una carpeta; saltado.",
    "wizard.profile_exists": (
        "  {path} ya existe; se usa tal cual y nunca se modifica."
    ),
    "wizard.profile_no_demo": (
        "  No se encontró el perfil demo en {demo}; crea {path} tú mismo "
        "(mira profiles/demo en el repositorio para ver la estructura)."
    ),
    "wizard.kind_skeleton": (
        "  [1] un esqueleto con la estructura de la demo y huecos bien marcados"
    ),
    "wizard.kind_demo": (
        "  [2] una copia del perfil demo ficticio, para probar lazyhunt primero"
    ),
    "wizard.kind_empty": (
        "  [3] un perfil vacío: arranca el bot y responde su /configurar guiado "
        "en Telegram (recomendado)"
    ),
    "wizard.kind_prompt": "  Empezar con [3]: ",
    "wizard.kind_retry": "  Responde 1, 2 o 3.",
    "wizard.kind.empty": "vacío",
    "wizard.kind.skeleton": "esqueleto",
    "wizard.kind.demo": "demo",
    "wizard.gmail": (
        "  La sincronización con Gmail es de solo lectura y usa un cliente OAuth "
        "que creas en tu propio proyecto de Google Cloud. Para activarla más "
        "tarde, sigue docs/gmail.md, ejecuta `uv run cli.py gmail-auth` y pon "
        "gmail_enabled = true en config.local.toml. Ahora no se configura nada."
    ),
    "wizard.planned": "Cambios previstos:",
    "wizard.env_set": "  {file}: definir {key} (valor oculto)",
    "wizard.env_replace": "  {file}: reemplazar {key} (valor oculto)",
    "wizard.env_missing": (
        "  {file}: {key} sin definir (el proveedor de IA lo necesita)"
    ),
    "wizard.create_profile": "  crear {path}/ ({kind})",
    "wizard.nothing_to_change": (
        "Nada que cambiar: tu configuración ya coincide con estas respuestas."
    ),
    "wizard.write_q": "¿Escribir estos cambios?",
    "wizard.nothing_written": "No se ha escrito nada.",
    "wizard.saved": (
        "Guardado. Los secretos solo en .env (modo 600), los ajustes en "
        "config.local.toml."
    ),
    "wizard.cancelled": "Configuración cancelada. No se ha escrito nada.",
    "wizard.invalid_toml": (
        "{file} no es TOML válido ({error}); corrígelo y vuelve a ejecutar init."
    ),
    "wizard.next": "Siguientes pasos:",
    "wizard.next_start_claim": (
        "  1. Arranca el bot con `uv run bot.py`. Muestra un código de un solo "
        "uso: envía /start <código> a tu bot en Telegram para ser su dueño."
    ),
    "wizard.next_start": (
        "  1. Arranca el bot con `uv run bot.py` y envíale /help en Telegram."
    ),
    "wizard.next_extension": (
        "  2. Carga la extensión: abre chrome://extensions, activa el Modo de "
        "desarrollador, pulsa Cargar descomprimida y elige la carpeta extension/."
    ),
    "wizard.next_pair": (
        "  3. Conéctala: envía {connect} al bot y, en las Opciones de la "
        "extensión › Frecuencia y bot, escribe el código en Emparejar con código "
        "(URL del puente http://127.0.0.1:{port}/bridge) y pulsa Emparejar."
    ),
    "wizard.next_portals": (
        "  4. En Opciones > Portales activa solo los portales cuyas condiciones "
        "hayas leído y aceptes; cada uno muestra su aviso y pide al navegador "
        "permiso para ese único dominio. Algunos portales (sobre todo LinkedIn) "
        "prohíben el acceso automatizado y tu cuenta podría quedar restringida."
    ),
    "wizard.next_profile_empty": (
        "  5. Tu perfil está vacío: envía {setup} al bot en Telegram para "
        "configurarlo y luego, en Opciones › Búsquedas, pulsa Sugerir desde mi "
        "perfil."
    ),
    "wizard.next_profile_fill": (
        "  5. Rellena tu perfil en {path} (mira su README.md)."
    ),
    "wizard.next_profile_create": (
        "  5. Crea la carpeta de tu perfil y define profile_dir "
        "(mira docs/getting-started.md)."
    ),
    "wizard.next_guide": "  Guía completa: docs/guia-rapida-es.md",
    "wizard.show_secret": (
        "¿Mostrar ahora el secreto del puente, para pegarlo a mano en la "
        "extensión en vez de emparejar?"
    ),
    "wizard.cli.owner_id": (
        "tu id numérico de usuario de Telegram, si ya lo sabes (opcional: el bot "
        "puede emparejarse contigo con un código de un solo uso)"
    ),
    "wizard.cli.owner_id_invalid": "--owner-id debe ser un número positivo",
}
