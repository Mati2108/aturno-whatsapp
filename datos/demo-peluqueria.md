# Peluquería Demo

Este archivo es la fuente del RAG para este negocio. Cuando carguemos un negocio
real, se reemplaza por el suyo y no hay que tocar código: el `business_id` sale
del nombre del archivo.

EL FORMATO IMPORTA, Y NO ES COSMÉTICO. Cada línea que empieza con `>` es una
pregunta —con sus sinónimos al lado— y lo que va debajo es su respuesta. El
índice corta por ahí: una unidad por pregunta, no una por sección.

Sin esas líneas, la sección entera queda como un solo fragmento y pasa lo que
`_por_pregunta` documenta: "el vector de un fragmento que habla de cinco cosas
no se parece lo suficiente a ninguna de las cinco". Este archivo estaba así, y
por eso "aceptan tarjeta?" no encontraba nada teniendo la respuesta escrita dos
renglones más abajo.

Es el mismo formato que escribe el panel de aturno, así que un negocio de verdad
lo genera solo.

## Servicios y precios

> ¿Cuánto sale un corte de pelo? precio corte cuánto cuesta cortarse el pelo valor
Corte de pelo: $8.000. Dura 30 minutos.

> ¿Cuánto sale una coloración? precio color teñido tintura cuánto cuesta teñirse
Coloración: $25.000. Dura 90 minutos.

> ¿Cuánto sale el perfilado de barba? precio barba afeitado
Perfilado de barba: $5.000. Dura 20 minutos.

> ¿Tienen combo de corte y barba? promo paquete los dos juntos
Corte + barba (combo): $11.000. Dura 45 minutos.

> ¿Los precios incluyen IVA? impuestos hay que sumar algo el precio es final
Sí, los precios incluyen IVA. Son finales.

> ¿Cobran más si tengo el pelo largo? adicional recargo pelo largo
No cobramos adicional por pelo largo.

## Horarios de atención

> ¿Qué horario tienen de lunes a viernes? a qué hora abren a qué hora cierran horario semana
Lunes a viernes de 9:00 a 18:00.

> ¿Abren los sábados? horario sábado fin de semana atienden sábados
Sábados de 9:00 a 13:00.

> ¿Atienden los domingos y feriados? abren domingo feriados
Domingos y feriados permanecemos cerrados.

> ¿Hasta qué hora puedo sacar turno? último turno del día cierre
El último turno del día se toma una hora antes del cierre.

## Cómo sacar y cancelar un turno

> ¿Cómo saco un turno? reservar pedir turno agendar
Los turnos se reservan por WhatsApp o desde la página web.

> ¿Cómo cancelo o cambio un turno? reprogramar cancelar avisar no puedo ir
Para cancelar o reprogramar avisá con al menos 4 horas de anticipación.

> ¿Qué pasa si no aviso y no voy? faltar no presentarse ausente
Si no avisás y no te presentás, el próximo turno requiere seña previa.

## Formas de pago

> ¿Qué formas de pago aceptan? cómo se paga medios de pago efectivo transferencia débito
Aceptamos efectivo, transferencia y tarjeta de débito.

> ¿Aceptan tarjeta de crédito o cuotas? crédito cuotas financiación tarjeta
No aceptamos tarjeta de crédito ni pagos en cuotas.

> ¿Piden seña? anticipo reserva señar cuánto hay que adelantar
Para coloración pedimos una seña del 30% al reservar.

## Dónde estamos y cómo llegar

> ¿Cuál es la dirección? dónde quedan dónde están en qué zona ubicación
Estamos en Av. Corrientes 3400, Almagro, Ciudad de Buenos Aires.

> ¿Cómo llego en subte? estación línea metro
A tres cuadras de la estación Medrano de la línea B de subte.

> ¿Qué colectivos paran cerca? bondi bus micro línea de colectivo
Paran cerca los colectivos 24, 26, 71 y 92.

> ¿Tienen estacionamiento? cochera dónde dejo el auto parking
No contamos con estacionamiento propio.

## Preguntas frecuentes

> ¿Puedo ir sin turno? por orden de llegada sin reserva atienden de una
Atendemos con turno previo, no por orden de llegada.

> ¿Tengo que ir con el pelo lavado? pelo sucio lavan antes hace falta lavarse
Se puede venir con el pelo sucio, lavamos antes de cortar.

> ¿Trabajan con productos sin amoníaco? alergia productos naturales sin amoníaco
Trabajamos con productos sin amoníaco a pedido, avisá al reservar.

> ¿Atienden chicos? menores niños nenes edad mínima
No atendemos menores de 12 años.
