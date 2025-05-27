import os
import csv
import argparse

def compile_results(input_dir, output_file):
      fieldnames = ['name', 'compression_size_MB', 'acc', 'precision', 'recall', 'f1']
      all_rows = []
      header_written = False

      if not os.path.isdir(input_dir):
            print(f"Error: Input directory '{input_dir}' not found.")
            return

      for filename in os.listdir(input_dir):
            if filename.endswith(".csv"):
                  filepath = os.path.join(input_dir, filename)
                  print(f"Processing: {filepath}")
                  try:
                        with open(filepath, 'r', newline='') as infile:
                              reader = csv.DictReader(infile)
                              if reader.fieldnames != fieldnames:
                                    print(f"Warning: Header mismatch in {filename}. Expected {fieldnames}, got {reader.fieldnames}. Skipping this file.")
                                    continue
                              
                              for row in reader:
                                    all_rows.append(row)
                  except Exception as e:
                        print(f"Error reading {filename}: {e}")

      if not all_rows:
            print(f"No files found to compile in '{input_dir}'.")
            return

      # Write all collected rows to the output file
      try:
            with open(output_file, 'w', newline='') as outfile:
                  writer = csv.DictWriter(outfile, fieldnames=fieldnames)
                  writer.writeheader()
                  writer.writerows(all_rows)
            print(f"Successfully compiled results to '{output_file}'.")
      except Exception as e:
            print(f"Error writing to output file '{output_file}': {e}")


if __name__ == "__main__":
      parser = argparse.ArgumentParser(description="Combine multiple CSV evaluation results into a single file.")

      parser.add_argument("-i", "--input_dir", type=str, required=True,
                        help="Directory containing individual CSV result files (e.g., 'results/').")
      parser.add_argument("-o", "--output_file", type=str, default="compiled_evaluation_results.csv",
                        help="Name of the combined output CSV file (e.g., 'combined_results.csv').")
      
      args = parser.parse_args()

      compile_results(args.input_dir, args.output_file)