# Guía rápida en español

Esta guía te lleva desde cero hasta la primera oferta puntuada en Telegram.
Es un resumen del [README](../README.md) y de la
[guía de instalación](getting-started.md) (en inglés), pensado para quien
prueba la beta. Calcula unos 20 minutos.

## 1. Qué es y qué esperar de una beta

lazyhunt es un asistente de búsqueda de empleo que se ejecuta en tu ordenador:
una extensión del navegador vigila los portales que elijas, un bot de Python
puntúa cada oferta nueva contra tu perfil y te lo cuenta todo por Telegram.
Nunca se inventa experiencia que no tienes.

`v2.0.0-beta.1` es una versión de prueba: habrá fallos y textos mejorables.
Si encuentras uno, abre un issue en
[GitHub Issues](https://github.com/SPujol98/lazyhunt/issues/new/choose) con la
plantilla que corresponda (fallo o propuesta). Un buen reporte incluye:

- qué hiciste, qué esperabas y qué pasó en realidad;
- los pasos para reproducirlo (mejor con el perfil de demostración, `profiles/demo/`);
- la versión o el commit, tu sistema operativo, la versión de Python y el navegador;
- el trozo del log que importa (ver [Dónde están los logs](#dónde-están-los-logs)).

> [!WARNING]
> No pegues nunca en un issue tu token de Telegram, el secreto del puente,
> claves de API ni datos personales (nombre, email, teléfono, tu CV). Revisa
> y tacha el log antes de pegarlo. Los problemas de seguridad no van en un
> issue público: sigue [SECURITY.md](../SECURITY.md).

## 2. Qué necesitas

| Necesitas | Para qué | Cómo comprobarlo |
| --- | --- | --- |
| **[uv](https://docs.astral.sh/uv/)** (trae Python 3.12 él solo) | Ejecuta el bot e instala sus dependencias | `uv --version` |
| **Telegram** | Es la única interfaz: creas tu propio bot con [@BotFather](https://t.me/BotFather) | |
| **Una IA**: Claude Code (por defecto) con la sesión iniciada | Puntúa ofertas y escribe cartas, con tu propia suscripción y sin clave de API | `claude auth status` |
| o **Codex CLI** con la sesión iniciada | Igual, con tu cuenta de OpenAI y sin clave de API | `codex login status` |
| o **una clave de API** | Anthropic, OpenAI o cualquier API compatible con OpenAI (Gemini, OpenRouter, Groq, Mistral); se paga por uso | [providers.md](providers.md) |
| o **un modelo local** | Ollama o LM Studio en tu ordenador: gratis y sin enviar nada fuera | [providers.md](providers.md) |
| **Chrome o Brave** | Aloja la extensión; déjalo abierto mientras escanea | |

Funciona en Linux, macOS y Windows (se desarrolla y prueba sobre todo en Linux).
Si `uv --version` falla, instálalo y abre un terminal nuevo:

```bash
# Linux y macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

No hace falta instalar Python: `uv sync` descarga Python 3.12 si no lo tienes.

> [!IMPORTANT]
> Los portales están **todos desactivados** por defecto. Algunos (sobre todo
> LinkedIn) prohíben en sus condiciones el acceso automatizado o las
> extensiones, y tu cuenta podría quedar restringida. Activa solo los que
> hayas leído y aceptes: cumplirlas es responsabilidad tuya.

## 3. Instalación

```bash
git clone https://github.com/SPujol98/lazyhunt.git lazyhunt
cd lazyhunt
uv sync
```

`uv sync` crea un entorno virtual e instala todo. No tienes que activarlo:
basta con poner `uv run` delante de cada comando.

## 4. Crea tu bot de Telegram

Hazlo una vez, en la app de Telegram, antes del asistente:

1. Busca [@BotFather](https://t.me/BotFather) (con la marca azul) y ábrelo.
2. Envía `/newbot`.
3. Escribe un nombre para tu bot (el que quieras, p. ej. `Mi buscador`).
4. Escribe un nombre de usuario que acabe en `bot` (p. ej. `mibuscador_bot`).
5. BotFather te responde con un token como `1234567890:AAH…`: copia el token
   **entero** y no lo compartas.

¿Ya tienes un bot? En @BotFather envía `/mybots`, elige tu bot y pulsa
**API Token**.

## 5. El asistente del terminal: `uv run cli.py init`

```bash
uv run cli.py init
```

El asistente solo configura el bot; lo que tiene que ver contigo llega después,
en Telegram. La primera pregunta es el idioma (en inglés y en español):
contesta `es` y **el resto del asistente te habla en español**. Aquí tienes qué
contestar en cada paso. Pulsa Intro para saltar un paso; no escribe nada hasta que
confirmas el resumen y puedes repetirlo cuando quieras.

1. **Idioma del bot** (*Language / Idioma*). `auto` (el idioma de tu
   sistema), `en` o `es`. Escribe `es` para tener el asistente, el bot, la
   línea de comandos y el panel en español. Los CV y las cartas siguen el
   idioma de cada oferta.
2. **Requisitos.** Comprueba Python, `uv`, que `claude` esté instalado y
   con la sesión iniciada, y si tienes `codex`. Si falta algo, te dice cómo
   arreglarlo y sigue.
3. **Proveedor de IA.** Pulsa Intro para `claude_code`, o elige otra IA (puedes
   escribir el nombre o su número): `codex_cli`, `anthropic` u
   `openai_compatible`. Mira [¿Otra IA?](#otra-ia) más abajo.
4. **Telegram.** Pega el token entero del paso 4; no se ve mientras lo
   pegas (es normal). Si lo que pegas no es un token de bot (por ejemplo tu
   API key de la IA), el asistente te lo dice y lo vuelve a pedir; solo
   muestra sus 4 últimos caracteres. **No hace falta tu id numérico**: al
   arrancar, el bot te da un código de un solo uso (paso 6). Si ya lo sabes,
   `uv run cli.py init --owner-id <id>` lo escribe.
5. **Secreto del puente.** Genera un secreto aleatorio largo que la extensión
   usa para hablar con el bot. No tienes que copiarlo: el bot le da a la
   extensión un código de emparejamiento (`/conectar`, paso 8).
6. **Carpeta del perfil.** Deja `profiles/me` y, en `Empezar con [3]`, pulsa
   Intro: la opción **3, un perfil vacío**, es la recomendada. El bot arranca
   en *modo configuración* y crea el perfil contigo con `/configurar`.
   (La 2 copia el perfil de demostración, de una persona ficticia, por si
   quieres curiosear antes.)
7. **Gmail.** Solo un enlace a [gmail.md](gmail.md); queda desactivado.

Al final contesta `s` (o `y`) a `¿Escribir estos cambios?`. Después te
ofrece mostrar el secreto del puente: no hace falta (emparejarás con
`/conectar`), pero si prefieres pegarlo a mano di `s`, o búscalo luego como
`CVA_BRIDGE_SECRET` en `.env`.

### ¿Otra IA?

Cualquier IA sigue las mismas instrucciones del programa, así que la
puntuación, los CV y las cartas funcionan con todas. En el paso 3 del asistente:

- **`codex_cli`**: tu Codex CLI de OpenAI, instalado y con la sesión iniciada
  (`codex login`). No pide clave.
- **`anthropic`**: te pide tu clave de API de Anthropic (no se ve al
  escribirla) y la guarda en `.env` como `ANTHROPIC_API_KEY`.
- **`openai_compatible`**: eliges un preajuste (OpenAI, Gemini, OpenRouter,
  Groq, Mistral, Ollama local, LM Studio local u Otro, escribiendo la URL),
  luego el modelo (ya viene rellenado; con Ollama o LM Studio escribes el
  nombre del modelo que hayas descargado) y la clave, que se guarda en `.env`
  como `CVA_LLM_API_KEY`. Un servidor local no usa la clave, pero tiene que
  haber una: vale cualquier texto, y con Intro se pone `local`.

Una diferencia: **la búsqueda en la web de datos de la empresa** para la carta
solo funciona con Claude Code y Codex CLI, porque pueden navegar; los modelos
por API no tienen esa herramienta, así que esa búsqueda aparece como no
disponible en vez de inventarse. Para cambiar de IA, vuelve a ejecutar `uv run cli.py init`: con Intro
conservas todo lo demás. Detalles: [providers.md](providers.md).

## 6. Arrancar el bot

```bash
uv run bot.py
```

**La primera vez**, el terminal muestra un código de un solo uso (y un enlace
`t.me` que lo envía por ti):

```text
lazyhunt aún no tiene dueño. Para ser su dueño, envía este mensaje a tu bot en Telegram:

    /start ABCD-EFGH
```

Envía exactamente eso a tu bot. Te contesta que ya eres su dueño y guarda tu id
como `owner_telegram_id` en `config.local.toml`; desde entonces **solo** te
contesta a ti. Hasta entonces ignora todos los mensajes, y a un código
incorrecto no responde. El código sirve una vez, durante 30 minutos; tras 5
intentos fallidos deja de funcionar: reinicia el bot para obtener otro. Solo se
muestra en un terminal, nunca en un archivo de log: aunque luego lo uses como
servicio, la primera vez arráncalo con `uv run bot.py` en un terminal.

Después envía `/help`. Con el perfil vacío contesta en **modo
configuración**: solo funcionan `/help`, `/start`, `/configurar` y
`/conectar` hasta que completes el perfil.

El bot funciona mientras ese terminal siga abierto. Para dejarlo en segundo
plano o que arranque al iniciar sesión (systemd, launchd o el Programador de
tareas de Windows), mira [running.md](running.md). La opción más rápida en
Linux o macOS es `tmux new -s lazyhunt 'uv run bot.py'` y salir con
`Ctrl+B` y luego `D`.

## 7. `/configurar` en Telegram

Envía `/configurar` (o `/setup`, funcionan los dos). El bot hace una pregunta
por mensaje, con botones cuando hay que elegir. Tarda unos **10 minutos**:

1. el idioma del bot;
2. tus datos de contacto: nombre, email, teléfono (opcional), ciudad y LinkedIn;
3. los puestos que buscas: las familias incluidas (IA y Backend) o tus
   propios puestos con **✏️ Otros puestos…**;
4. tu nivel: Junior, Mid o Senior;
5. los idiomas que hablas y tu nivel en cada uno;
6. tus habilidades principales;
7. años de experiencia, último puesto y empresa;
8. tu formación más alta;
9. modalidades (Remoto, Híbrido, Presencial), la ciudad y el país donde
   quieres trabajar (el país de tu ciudad sale con un toque, p. ej.
   «España (por Barcelona)»);
10. un sueldo mínimo bruto anual (opcional);
11. los idiomas en que puede estar una oferta (las demás se descartan);
12. los portales que quieres activar; cada uno muestra antes su aviso sobre
    las condiciones de uso;
13. tus **CV base en PDF**, uno por puesto e idioma;
14. un resumen con **✏️ Editar** y **✅ Confirmar**.

Detalles útiles:

- Cada paso tiene **✖️ Cancelar**, y desde el segundo también **◀️ Atrás**.
  Las preguntas opcionales tienen **Omitir ⏭**, y las de elegir varias
  opciones se cierran con **Listo ✔️**.
- **Los CV en PDF:** envía cada uno como archivo (📎 → Archivo), no como
  foto; máximo 10 MB y 10 páginas. Pulsa una línea de la lista para elegir a
  qué puesto e idioma va el siguiente PDF. Puedes saltarlos con
  **Omitir este ⏭** u **Omitir todos ⏭⏭** y añadirlos más tarde con
  `/configurar cvs`, pero hasta entonces el bot no puede entregar un CV para
  ellos.
- **Nada se guarda hasta que pulsas ✅ Confirmar**, y Confirmar es todo o
  nada: si algo falla, todos los archivos quedan como estaban, tus PDF se
  conservan y puedes corregir la causa y pulsar Confirmar otra vez.
- Si dejas la configuración parada 30 minutos, caduca: envía `/configurar`
  para empezar de nuevo.

**Después de Confirmar** el bot guarda tu perfil, activa los portales que
elegiste y pasa a modo normal sin reiniciar. La respuesta termina con el
siguiente paso: abrir la extensión y sugerir búsquedas. Todas las preguntas y
archivos: [onboarding.md](onboarding.md).

## 8. La extensión

La extensión no está en ninguna tienda: la cargas desde el código del
repositorio. Sus textos siguen el idioma de tu navegador.

1. Abre `chrome://extensions` (en Brave, `brave://extensions`), activa el
   **Modo de desarrollador**, pulsa **Cargar extensión sin empaquetar**
   (*Load unpacked* si tu navegador está en inglés) y elige la carpeta `extension/` del repositorio.
2. Envía `/conectar` al bot: te responde un código de un solo uso. Abre las
   **Opciones** de la extensión › **Frecuencia y bot**, escríbelo en
   **Emparejar con código** y pulsa **Emparejar**: la URL del puente
   (`http://127.0.0.1:8765/bridge`) y el secreto se rellenan y se guardan
   solos. El código sirve una vez, durante 10 minutos. (Pegar el secreto a mano
   en **Secreto del puente** sigue funcionando: es `CVA_BRIDGE_SECRET` en
   `.env`; pulsa luego **Guardar cambios**.)
3. En **Portales**, activa **los mismos** que elegiste en `/configurar`: un
   portal solo se escanea si está activo en el bot y en la extensión. Cada
   interruptor muestra el aviso de condiciones y pide al navegador permiso
   para ese único sitio.
4. En **Búsquedas**, pulsa **Sugerir desde mi perfil**. Propone búsquedas
   hechas con tus respuestas (los puestos que elegiste, en tu ciudad si
   aceptas presencial o híbrido, más una en remoto si lo aceptas). Quédate
   con las que quieras, añade las tuyas y pulsa **Guardar cambios**. Mientras
   no tengas ninguna, la ventana de la extensión dice "Todavía no hay
   búsquedas" y te trae aquí.

**Tu primera oferta.** No hace falta esperar: pulsa **Comprobar ahora** en la
ventana de la extensión (su icono en la barra), o pega el enlace de una
oferta en Telegram con `/analyze <url>`. Si no puede revisar nada, lo dice al
momento (el bot no responde, rechaza el secreto, falta terminar
`/configurar`, no hay búsquedas o ningún portal está activo) y **Pausar**
detiene una ronda en curso. Después las rondas se repiten solas
cada 60 minutos (se cambia en **Frecuencia y bot**).

**La tarjeta en Telegram** trae la puntuación de 0 a 100, el veredicto
(*aplicar*, *aplicar con matices* o *descartar*), lo que cumples y lo que no,
y estos botones:

- **📄 Generar CV**: un PDF de una página. Antes puedes cambiar
  **Idioma: …** (ES/EN/CA) y **Variante: …** en la propia tarjeta.
- **✉️ Carta de presentación**: respeta tu carta base y solo escribe lo que
  cambia en cada oferta, con las fuentes de cada dato sobre la empresa.
  **✅ Usar esta** la guarda y te la envía también en PDF.
- **💶 Investigar salario**, **✅ Marcar enviada** (o `/sent <id>`) y
  **🗑 Descartar**.
- La última fila, **🔎 En LinkedIn / En Google / En InfoJobs**, busca la
  misma oferta (puesto + empresa) en otro sitio, por si prefieres
  presentarte allí.

Los avisos **⚠️** de la tarjeta te señalan lo que choca con tus preferencias,
por ejemplo *⚠️ Modalidad presencial; aceptas: remoto* o un *Salario por
debajo de tu mínimo*. No descartan la oferta: decides tú.

¿Te vas de vacaciones? `/pausa` (o `/pause`) detiene todo lo automático y
`/reanudar` lo vuelve a activar. La ventana de la extensión tiene el botón
**Pausar**.

## 9. Cambiar tu perfil después

Con `/configurar <sección>` el bot vuelve a preguntar solo esa parte, con tus
respuestas actuales ya puestas (botón **Mantener …**). Por ejemplo
`/configurar salario`. Las secciones (no importan mayúsculas ni tildes):

| Sección | Qué cambia |
| --- | --- |
| `contacto` | nombre, email, teléfono, ciudad, LinkedIn |
| `puestos` | los puestos que buscas |
| `nivel` | tu nivel |
| `trayectoria` | idiomas que hablas, habilidades, experiencia, formación |
| `ubicacion` | modalidades, ciudad y país de trabajo |
| `salario` | sueldo mínimo y moneda |
| `idiomas-ofertas` | idiomas aceptados en las ofertas |
| `portales` | portales activos |
| `cvs` | tus CV base en PDF |

También puedes hacerlo en la extensión: **Opciones › Perfil › Cargar desde el
bot**, cambiar lo que quieras y pulsar **Guardar** en cada parte. Los datos de
contacto, los portales y los CV solo se cambian con `/configurar <sección>`.

## 10. Problemas frecuentes

**El bot no contesta en Telegram.**
Comprueba que sigue en marcha (el terminal o tu servicio). `CVA_TELEGRAM_TOKEN`
en `.env` debe ser el token de BotFather. Si Telegram lo rechaza, el bot lo
dice en una línea al arrancar («Telegram ha rechazado el token del bot…») y
se para, sin mostrar el token. Si aún no tiene dueño
(`owner_telegram_id` sin definir o `0`), solo atiende `/start <código>` con el
código que muestra su terminal; tras 5 códigos incorrectos o 30 minutos,
reinícialo para obtener otro. Ya emparejado, solo contesta al id guardado. `uv run cli.py check` carga tu configuración sin Telegram y
nombra el ajuste que esté mal.

**Solo contesta a `/configurar`.** Está en modo configuración: termina el
cuestionario y pulsa **✅ Confirmar**.

**"No he podido guardar tus respuestas".** No se ha cambiado nada y tus PDF se
conservan. El mensaje nombra el archivo y la causa (suele ser un
`preferences.toml` o `config.local.toml` editado a mano que ya no se lee):
corrígelo y pulsa **Confirmar** otra vez.

**La extensión dice "Bot sin conectar".** El bot tiene que estar en marcha y
la URL del puente debe ser exactamente `http://127.0.0.1:8765/bridge`. Lo más
fácil es volver a emparejar: `/conectar` en el bot y el código en **Emparejar
con código**. Un secreto escrito a mano debe ser el mismo que
`CVA_BRIDGE_SECRET`. Si al sugerir búsquedas sale
"El bot ha rechazado el secreto.", el secreto no coincide. Si el log dice
`bridge bind failed on 127.0.0.1:8765`, otro programa usa ese puerto: pon otro
`bridge_port` en `config.local.toml`, reinicia y usa ese puerto en la URL.

**Las ofertas no se puntúan.** Ejecuta `claude auth status`; si no hay sesión,
`claude auth login`. Si funciona en tu terminal pero no como servicio, mira las
notas de `PATH` en [running.md](running.md) o pon la ruta completa en
`claude_code_path`.

**Una búsqueda no encuentra nada.** Ábrela a mano en el navegador: puede que
se haya cerrado tu sesión, haya un captcha o simplemente no haya ofertas
nuevas. Los filtros de títulos y de ubicación también pueden descartarlo todo.
Límites de cada portal: [portals.md](portals.md).

### Dónde están los logs

- **Bot:** el terminal donde lo arrancaste, `journalctl --user -u lazyhunt`
  (servicio de Linux) o `data/bot.log` (macOS y Windows con
  [running.md](running.md)).
- **Extensión:** `chrome://extensions` › lazyhunt › **service worker** abre su
  consola.

Más casos: [Troubleshooting](../README.md#troubleshooting) del README.

## 11. Actualizar, desinstalar y privacidad

**Actualizar:** `git pull` y `uv sync` en la carpeta `lazyhunt`, reinicia el
bot y pulsa el botón de recargar de lazyhunt en `chrome://extensions`. Tu
`.env`, `config.local.toml`, `data/` y `profiles/me/` no se tocan.

**Desinstalar:** para el bot (y su servicio si lo creaste), quita la extensión
en `chrome://extensions`, guarda lo que quieras de `profiles/me/` y
`data/app.db` y borra la carpeta. Revoca el token con BotFather (`/revoke`, o
`/deletebot`) y cualquier clave de API que creaste para lazyhunt.

**Privacidad:** todo vive en tu ordenador; el puente solo escucha en
`127.0.0.1` y exige el secreto. Salen de tu equipo el texto de las ofertas y
las partes necesarias de tu perfil (hacia tu proveedor de IA) y las tarjetas
(hacia Telegram). El bot solo contesta a tu id. Detalles:
[Privacy and security](../README.md#privacy-and-security).
