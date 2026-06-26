from bloqade.analog import var
from bloqade.analog.atom_arrangement import Square

import numpy as np

# Este código define un programa adiabático para un arreglo cuadrado de átomos, donde se varía la detuning máxima y se asignan valores específicos para la amplitud de Rabi y la detuning. 
# Luego, se ejecuta una simulación utilizando el emulador de Python desarrollado por Bloqade, que puede ser más rápido en tareas específicas. 
# También se muestra cómo ejecutar la simulación en el emulador local de Amazon Braket y cómo enviar el programa al hardware cuántico real, el procesador Aquila.

adiabatic_durations = [0.4, 3.2, 0.4]

# Parámetro que se irá variando
max_detuning = var("max_detuning")

adiabatic_program = (
    # Define a 3x3 square lattice
    Square(3, lattice_spacing="lattice_spacing")
    # Configura cómo varía la intesidad del láser (amplitud de Rabi)
    .rydberg.rabi.amplitude.uniform.piecewise_linear(
        durations=adiabatic_durations, values=[0.0, "max_rabi", "max_rabi", 0.0]
    )
    # Configura cómo varía la detuning (desajuste de fase del láser)
    .detuning.uniform.piecewise_linear(
        durations=adiabatic_durations,
        values=[
            -max_detuning, 
            -max_detuning,
            max_detuning,
            max_detuning,
        ],
    )
    # Transformamos las variables simbólicas en valores concretos 
    .assign(max_rabi=15.8, max_detuning=16.33)
    .batch_assign(lattice_spacing=np.arange(4.0, 7.0, 0.5))
)

# Métodos de ejecución

# Forma 1: Ejecuta una simulación en el emulador local que proporciona AMazon Braket
#emu_results = adiabatic_program.braket.local_emulator().run(10000)  

# Forma 2: Utiliza el emulador de Python desarrollado por Bloqade, que puede ser más rápido en tareas específicas
print(f"--- --- Ejecutando simulación local --- ---\n\n")
faster_emu_results = adiabatic_program.bloqade.python().run(10000) 
print(f"\n\n--- --- ¡Simulación completada con éxito! --- ---\n\n")
print(faster_emu_results.report())

report = faster_emu_results.report()

# Muestra el conteo de los estados finales detectados
for i, count in enumerate(report.counts()):
    print(f"´{i}) Resultado para el paso {i} del barrido: {count}")


print(f"Probabilidad estadística de cada configuración: \n {report.probabilities()}") 


# Forma 3: Esto envía el programa al hardware cuántico real, el procesador Aquila
#hw_results = adiabatic_program.parallelize(24).braket.aquila().run_async(100)