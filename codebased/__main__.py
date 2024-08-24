from pathlib import Path


def main():
    with open(Path(__file__).parent / "GREETING.txt") as f:
        print(f.read())


if __name__ == '__main__':
    main()
