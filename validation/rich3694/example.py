from rich.console import Console
from rich.prompt import IntPrompt

choices = ["Lorem ipsum", "Dolor sit amet", "consectetur adipiscing elit"]
console = Console()
console.print("Select one of:")
for number, label in enumerate(choices, 1):
    console.print(f"{number}. {label}", markup=False)

number = IntPrompt.ask(
    "Choice",
    choices=[str(i) for i in range(1, len(choices) + 1)],
    show_choices=False,
    console=console,
)
selected = choices[number - 1]
