from bloqade.analog import piecewise_linear, var
from bloqade.analog.ir.location import Square
import numpy as np

# Este código es otro ejemplo sobre la máxima componibilidad (igual que prueba2.py)

# Create a geometry without worrying about pulses yet
#   "lattice_spacing" es una variable simbólica, por lo que la distancia entre átomos se define despúes
square_lattice = Square(3, lattice_spacing="lattice_spacing") 

# Se definen las waveforms de forma independiente
#   Amplitud de Rabi
adiabatic_durations = [0.8, 2.4, 0.8]
separate_rabi_amp_wf = piecewise_linear(
    durations=adiabatic_durations, values=[0.0, "max_rabi", "max_rabi", 0.0]
)
#  Detuning
max_detuning = var("max_detuning")
separate_rabi_detuning = piecewise_linear(
    durations=adiabatic_durations,
    values=[-max_detuning, -max_detuning, max_detuning, max_detuning],
)

# Now bring it all together!
# And why not sprinkle in some parameter sweeps for fun?
full_program = (
    square_lattice.rydberg.rabi.amplitude.uniform.apply(separate_rabi_amp_wf)
    .detuning.uniform.apply(separate_rabi_detuning)
    .assign(max_rabi=15.8, max_detuning=16.33)
    .batch_assign(
        # Aquí es donde ocurre el barrido de parámetros
        lattice_spacing=np.arange(5.5, 8.0, 0.5),
        max_rabi=np.linspace(2 * np.pi * 0.5, 2 * np.pi * 2.5, 6),
    )
)

# Ejecución del programa
res = full_program.bloqade.python().run(1000, solver_name="vern9")
print(f"Ejecución programa: \n{res.report().counts()}\n\n")