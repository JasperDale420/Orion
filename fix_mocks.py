import glob

files = glob.glob("tests/**/*.py", recursive=True)

for file in files:
    with open(file) as f:
        content = f.read()

    if (
        'patch("orion.execution.execution_engine.AlpacaTradingConnector")' in content
        and "AlpacaOptionsConnector" not in content
    ):
        print(f"Fixing {file}")
        content = content.replace(
            'patch("orion.execution.execution_engine.AlpacaTradingConnector"),',
            'patch("orion.execution.execution_engine.AlpacaTradingConnector"),\n            patch("orion.execution.execution_engine.AlpacaOptionsConnector"),',
        )
        with open(file, "w") as f:
            f.write(content)
