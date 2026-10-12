#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Generate publication statistics from figshare articles CSV.
Outputs a markdown table showing publications per author per year.
"""

import pandas as pd
import sys
import argparse
from pathlib import Path

def generate_statistics(all_csv='figshare_articles_all.csv', dedup_csv='figshare_articles.csv'):
    """
    Read the figshare articles CSVs and generate statistics.
    
    Args:
        all_csv: CSV file with all publications (includes duplicates for multi-author papers)
        dedup_csv: CSV file with deduplicated publications (for calculating true totals)
    
    Returns:
        A markdown table string showing statistics.
    """
    try:
        # Read the per-author CSV file (includes duplicates for multi-author papers)
        df_all = pd.read_csv(all_csv)
        
        # Read the deduplicated CSV file (for accurate totals)
        df_dedup = pd.read_csv(dedup_csv)
        
        if df_all.empty:
            return "No publication data available."
        
        # Ensure we have the required columns
        if 'author' not in df_all.columns or 'online_year' not in df_all.columns:
            return "Error: Required columns (author, online_year) not found in all articles CSV."
        
        if 'online_year' not in df_dedup.columns:
            return "Error: Required column (online_year) not found in deduplicated CSV."
        
        for frame in (df_all, df_dedup):
            frame['online_year'] = frame['online_year'].apply(
                lambda year: str(int(year)) if pd.notna(year) else 'Undated')

        # Group by author and year, count publications per author
        stats = df_all.groupby(['author', 'online_year']).size().reset_index(name='count')
        
        # Pivot to get years as columns
        pivot = stats.pivot(index='author', columns='online_year', values='count').fillna(0).astype(int)
        
        # Sort columns (years) in descending order (most recent first)
        years = sorted((year for year in pivot.columns if year != 'Undated'), reverse=True)
        if 'Undated' in pivot.columns:
            years.append('Undated')
        pivot = pivot[years]
        
        # Calculate total per author (from their individual publications)
        pivot['Total'] = pivot.sum(axis=1)
        
        # Sort by total publications (descending)
        pivot = pivot.sort_values('Total', ascending=False)
        
        # Calculate actual yearly totals from deduplicated data
        dedup_by_year = df_dedup.groupby('online_year').size()
        
        # Generate markdown table
        md_lines = ["# Publication statistics by author and year", ""]
        md_lines.append(f"**Total Authors:** {len(pivot)}\n")
        md_lines.append(f"**Total Publications (deduplicated):** {len(df_dedup)}\n")
        md_lines.append("")
        
        # Create table header
        headers = ['**Author**', '**Total**'] + [str(year) for year in pivot.columns if year != 'Total']
        md_lines.append('| ' + ' | '.join(headers) + ' |')
        md_lines.append('| ' + ' | '.join(['---' for _ in headers]) + ' |')
        
        # Create table rows
        for author, row in pivot.iterrows():
            values = [f"**{author}**", f"**{int(row['Total'])}**"] + [str(int(row[year])) if row[year] > 0 else '-' for year in pivot.columns if year != 'Total']
            md_lines.append('| ' + ' | '.join(values) + ' |')
        
        # Add yearly totals row using deduplicated data
        year_columns = [year for year in pivot.columns if year != 'Total']
        year_totals = ['**Total (unique)**', f"**{len(df_dedup)}**"] + [str(int(dedup_by_year.get(year, 0))) for year in year_columns]
        md_lines.append('| ' + ' | '.join(year_totals) + ' |')
        
        return '\n'.join(md_lines)
    
    except FileNotFoundError as e:
        return f"Error: File not found - {e.filename}"
    except Exception as e:
        return f"Error generating statistics: {str(e)}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate publication statistics from FigShare articles CSV files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '--all-csv',
        type=str,
        default='figshare_articles_all.csv',
        help='Path to CSV file with all publications (includes duplicates for multi-author papers)'
    )
    parser.add_argument(
        '--dedup-csv',
        type=str,
        default='figshare_articles.csv',
        help='Path to CSV file with deduplicated publications (for accurate total counts)'
    )
    
    args = parser.parse_args()
    
    # Generate and print statistics
    stats = generate_statistics(args.all_csv, args.dedup_csv)
    print(stats)
    if stats.startswith("Error"):
        raise SystemExit(1)

