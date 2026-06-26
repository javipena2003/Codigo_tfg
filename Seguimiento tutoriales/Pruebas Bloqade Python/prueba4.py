# Método para runnear de forma más eficiente:
#   Si el código no usa todo el FOV ("Field of View") o todos los qubits posibles, se puede paralelizar el programa para aprovechar al máximo el hardware.

# you could just run your program and leave free qubits on the table...
program_with_few_atoms.braket.aquila().run_async(100)
# ...or you can take all you can get!
program_with_few_atoms.parallelize(24).braket.aquila(24).run_async(100)