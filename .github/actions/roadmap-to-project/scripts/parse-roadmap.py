    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

files = []

for line in args.files.splitlines():
