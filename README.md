# portfolio-data-mining
This is the source code for data mining the Portfolio Thesis Data

Install python dependencies:

```bash
conda create -n scraping python=3.14.6
conda activate scraping
pip install -r requirements.txt
```

[S&P 500 companies list](https://github.com/datasets/s-and-p-500-companies/blob/main/data/constituents.csv)

## FastAPI Server
To run the FastAPI server, use the following command:

```bash
uvicorn app:app --reload
```

## collect_historical_news.py
This script collects historical news articles related to a list of companies or queries using the GDELT API. It fetches news articles within a specified date range and saves the results to a CSV file.

How to use:
1. Ensure you have the required dependencies installed.
2. Be sure that you leave the companies you want to farm in the directory `input/s&p500.csv` (or modify the path in the script).
3. Run the script using Python:

    ```bash
    python collect_historical_news.py
    ```
4. The script will fetch news articles for the specified companies and save the results to a CSV file in the output/news/links directory.
5. The script will save your progress in a checkpoint CSV file, allowing you to resume the process if interrupted.
