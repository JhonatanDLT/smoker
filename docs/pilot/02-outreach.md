# Outreach

## Principles

1. **Lead with a finding, not a pitch.** "You have 4 tables without a foreign
   key" beats "we generate synthetic data".
2. **One call to action.** A 20-minute call, not "let me know if you're
   interested".
3. **Short.** Under 120 words for the first message.
4. **No attachment on the first contact.** Offer the audit, do not send a PDF
   that nobody asked for.
5. **Personalize one line.** A repo, a job post, a migration, a public post.

## Message 1 — email

Subject: `Una cosa que vi en su esquema de Postgres`

> Hola {nombre}:
>
> Vi que {hallazgo específico: están migrando a Postgres 17 / tienen 4 tablas sin
> FK / su repo usa Prisma con 38 modelos}. Eso suele romper el staging: los datos
> de prueba violan constraints y el equipo termina copiando producción.
>
> Preparé una auditoría de una página de su esquema (sin leer datos): tablas,
> FKs, invariantes candidatas y 3 riesgos concretos.
>
> ¿Te la mando y la revisamos en 20 minutos?
>
> {firma}

## Message 2 — LinkedIn

> Hola {nombre}, vi {trigger}. Estoy haciendo auditorías de esquemas Postgres
> para equipos que no quieren copiar producción al staging. ¿Te interesa que te
> mande la de ustedes? Es una página, sin acceso a datos.

## Message 3 — follow-up (day 4)

> {nombre}, te dejo el hallazgo principal por si sirve aunque no hablemos:
>
> {una frase con el riesgo concreto, por ejemplo: "billing.invoices no tiene FK a
> accounts, así que cualquier borrado deja facturas huérfanas y el staging no lo
> detecta".}
>
> Si quieres, te mando el resto de la auditoría. Si no, sin problema.

## Message 4 — breakup (day 10)

> {nombre}, cierro el tema para no insistir. Si en el próximo trimestre hay
> migración, auditoría o un entorno nuevo, escríbeme y te mando la auditoría
> actualizada.

## Objection handling

| Objection | Response |
|---|---|
| "Faker nos basta" | "Faker genera valores plausibles, no datos que cumplan tus constraints. ¿Puedo mostrarte cuántas filas de un seed típico violan las tuyas?" |
| "Copiamos producción, es más rápido" | "Hasta la auditoría. ¿Qué pasa si ese entorno aparece en un incidente o en una revisión de LFPDPPP?" |
| "No podemos dar acceso al esquema" | "No lo necesito. Me pasas un dump o corres `smoker introspect` y me mandas el JSON. El audit se hace con metadatos." |
| "Ya tenemos un DBA" | "Perfecto, él va a ser el primero en usar el reporte. La pregunta es si hoy puede probar que el staging cumple todas las constraints." |
| "Es caro" | "El piloto cuesta menos que una semana de un ingeniero. Si no llegamos a cero violaciones, no pagas." |
| "¿Esto es IA?" | "No. Es introspección del catálogo de Postgres, reglas y verificación SQL. Determinista y reproducible." |

## Call agenda (20 minutes)

1. **2 min** — Context: what they run, team size.
2. **5 min** — How they populate staging today and what breaks.
3. **5 min** — Walk through one finding from the audit.
4. **5 min** — Propose the two-week pilot and the price.
5. **3 min** — Next step: who signs, when we start, what access is needed.

End every call with a date, not with "I'll send you information".

## Personalization variables

```
{nombre}        first name
{empresa}       company
{trigger}       migration / audit / new environment / incident
{hallazgo}      one concrete finding from the audit
{rol}           tech lead / platform engineer / CTO
```
