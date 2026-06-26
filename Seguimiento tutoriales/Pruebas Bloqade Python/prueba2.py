from bloqade.analog import start

# Este código ejemplifica la Máxima Componibilidad: 
#   se pueden construir múltiples variantes de un programa cuántico a partir de una base común sin necesidad de reescribir toda la lógica.

# Posición inicial: un átomo en (0,0)
initial_geometry = start.add_position((0, 0))

# Referencia de la amplitud del láser (frecuencia de Rabi). Esto es lo que se reusa
target_rabi_wf = initial_geometry.rydberg.rabi.amplitude.uniform

# Ejecución de dos variantes del programa, con diferentes variaciones de la amplitud de Rabi a lo largo del tiempo
program_1 = target_rabi_wf.piecewise_linear(
    durations=[0.4, 2.1, 0.4], values=[0, 15.8, 15.8, 0]
)
program_1.show() # Esto abre una pestaña en el navegador con los gráficos del láser

program_2 = target_rabi_wf.piecewise_linear(
    durations=[0.5, 1.0, 0.5], values=[0, 10.0, 11.0, 0]
).constant(duration=0.4, value=5.1)


# Ejecución y visualización de los resultados
res = program_1.bloqade.python().run(100)
res.report().show()

#res2 = program_2.bloqade.python().run(100)
#print(f"Ejecución programa 2: \n{res2.report().counts()}")