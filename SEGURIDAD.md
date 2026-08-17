# Antes de que esto lo use un cliente real

Todo lo que hay que rotar, sacar o cerrar. Está escrito ahora, mientras se
acumula, porque estas cosas no se recuerdan solas: se descubren cuando ya
pasaron.

Ordenado por lo que más expone.

---

## 🔴 Rotar. Estas credenciales viajaron por un chat

Cualquier credencial que se pegó en una conversación hay que darla por
comprometida, aunque el chat sea privado. No es paranoia: queda en un historial
que se sincroniza, se respalda y sobrevive a la máquina donde se escribió.

| Credencial | Dónde se rota | Dónde hay que actualizarla después |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys | Render: `aturno-whatsapp` |
| `TWILIO_AUTH_TOKEN` | console.twilio.com → Account → API keys | Render: `aturno-whatsapp` |
| `GEMINI_API_KEY` | aistudio.google.com | Render: `aturno-whatsapp` |
| Secreto del panel | Generar uno nuevo (ver abajo) | Render: **los dos** servicios |

El `TWILIO_ACCOUNT_SID` no es secreto —es un identificador— pero conviene
rotar el token igual porque viajaron juntos.

Para el secreto del panel:

    python -c "import secrets; print(secrets.token_urlsafe(48))"

Va en `WHATSAPP_BOT_SECRET` (aturno-backend) y `PANEL_SECRETO`
(aturno-whatsapp), con **el mismo valor**. Mientras no coincidan, el panel no
puede contestar; el bot sigue atendiendo normal, que es la falla en la
dirección correcta.

---

## 🔴 Las credenciales de Firebase están en el historial de git

El archivo `aturno/backend/serviceAccount.json` se commiteó en `769d505` y su
borrado todavía está sin commitear. **Borrarlo no lo saca del historial**:
cualquiera con acceso al repo puede recuperarlo de ese commit.

El repo `Mati2108/Aturno` es privado hoy, así que no es una emergencia. Pero
esa clave da acceso de administrador a toda la base: si el repo se hace público
por error, o se le da acceso a alguien más, ya está afuera.

Lo correcto: **rotar la clave en la consola de Firebase** (Configuración del
proyecto → Cuentas de servicio → Generar nueva clave privada) y revocar la
vieja. Reescribir el historial de git no alcanza si la clave sigue siendo
válida.

---

## 🟡 Endpoints públicos que cuentan de más

Los dos son del bot y no requieren autenticación:

- **`GET /diagnostico`** — dice qué credencial está mal, con su largo y sus
  primeros 7 caracteres. Nunca el valor, pero un prefijo y un largo son un
  punto de partida. Existía para diagnosticar el despliegue a ciegas; ese
  problema ya está resuelto.
- **`GET /cupo`** — expone cuántos mensajes mandó la cuenta de Twilio.

Antes de repartir la URL: dejarlos detrás del mismo secreto que el panel, o
sacarlos. `GET /salud` puede quedar público — no dice nada que no se vea desde
afuera, y el despertador lo necesita.

---

## 🟡 Puertas traseras que quedaron abiertas para desarrollar

**`TRIAL_PERMANENTE_EMAILS`** le da trial infinito a las cuentas que estén en
la lista. Es correcto para desarrollar, pero hay que revisarlo antes de cobrar:
una cuenta ahí adentro nunca ve el vencimiento ni el bloqueo, que es justo lo
que hay que poder probar antes de venderle a alguien.

**`server.js:865`** todavía tiene la lista vieja escrita en el código:

```js
const testingEmails = ['matiascalo2@gmail.com'];
```

Es la que quedó sin migrar dentro de `validatePublicLimit`. Son dos fuentes
para la misma decisión: el día que se cambie una y no la otra, va a haber una
cuenta con trial en un endpoint y sin trial en otro, y eso se descubre tarde y
mal.

**El trial incluye el asistente de WhatsApp** (`server.js`, features del
trial). Es una decisión de producto tomada a propósito —nadie paga por algo que
no pudo probar— pero conviene volver a mirarla cuando haya volumen: hoy no hay
tope de conversaciones durante el trial.

---

## 🟡 Lo que hay que apretar antes de abrir el número

- **`VALIDAR_FIRMA` tiene que quedar en `true`.** Es lo único que impide que
  cualquiera postee turnos falsos al webhook, que es una URL pública.
- **No hay límite por teléfono.** Nada impide que un mismo número mande cien
  mensajes y gaste el saldo de la API. Con el cupo de Twilio esto está tapado
  por accidente; con un número propio, deja de estarlo.
- **La sesión no vence.** Quien deja una conversación a medias sigue en el
  mismo paso a la semana siguiente.
- **El nombre del cliente entra sin escapar al mail del dueño**
  (`emailService.js`, cuerpo y asunto). El bot ya lo limpia de su lado, pero el
  formulario de la web tiene el mismo agujero desde antes: falta escapar al
  escribir el mail, que es donde se arregla para los dos.

---

## 🟢 Basura de las pruebas, para limpiar

- **Turnos de prueba en la agenda.** `verificar_turno.py` cancela lo que crea,
  pero las pruebas manuales no. Buscar los de "Prueba WhatsApp", "Ana Pérez",
  "Bruno Díaz", "Carla Gómez", "Diego Luna" y "Matías Fontane".
- **La conversación de demo** del número `+5491155667788` en
  `whatsapp_conversations`. No existe: era para que se viera algo en el panel.
- **El negocio `aturno`** tiene datos mezclados: se llama "Aturno", el rubro
  dice `sports` y el único servicio es "Dentista". Sirve para probar; no sirve
  para mostrarle a nadie.
- **`Juan Demo`** como nombre de profesional, en cualquier demo o video.

---

## ⚪ Lo que NO hay que hacer

- **No borres el `.env` local.** Está en `.gitignore` en los dos repos y ya se
  verificó que no aparece en el historial. Es el único lugar donde estas
  credenciales viven bien.
- **No pongas el secreto del panel en el repo**, ni en `render.yaml`, ni en un
  comentario. Solo en las variables de entorno de Render.
- **No hagas público `Mati2108/Aturno`** hasta rotar la clave de Firebase.
