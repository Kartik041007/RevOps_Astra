
from pathlib import Path
import pandas as pd

# Project ke data folder ka path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def inspect_datasets():
    if not DATA_DIR.exists():
        raise FileNotFoundError(
            f"Data folder nahi mila: {DATA_DIR}"
        )

    csv_files = sorted(DATA_DIR.glob("*.csv"))

    if not csv_files:
        raise FileNotFoundError(
            f"Koi CSV file nahi mili: {DATA_DIR}"
        )

    print(f"Dataset folder: {DATA_DIR}")
    print(f"CSV files found: {len(csv_files)}")

    datasets = {}

    for file_path in csv_files:
        try:
            df = pd.read_csv(file_path)
            datasets[file_path.stem] = df

            print("\n" + "=" * 55)
            print(f"FILE: {file_path.name}")
            print(f"Rows: {df.shape[0]}")
            print(f"Columns: {df.shape[1]}")
            print(f"Duplicate rows: {df.duplicated().sum()}")

            print("\nColumn names:")
            print(df.columns.tolist())

            missing = df.isnull().sum()
            missing = missing[missing > 0]

            print("\nMissing values:")
            if missing.empty:
                print("None")
            else:
                print(missing.to_string())

            print("\nFirst 2 rows:")
            print(df.head(2).to_string(index=False))

        except Exception as exc:
            print(f"\nERROR reading {file_path.name}: {exc}")

    return datasets


if __name__ == "__main__":
    datasets = inspect_datasets()
