# pdf2tables

# Repository Structure

data/
    raw_pdf/
    preprocessed_pdf/
    raw_textract_json/
    processed_textract_json/
    export_csv/

preprocessing_utils.py
preprocessing.ipynb
textract-utils.py
textract.ipynb
ai_correction_utils.py
ai_correction.ipynb

app.py
assets/
    dashAgGridComponentFunctions.js

pyproject.toml
poetry.lock

# Workflow
```mermaid
---
title: Workflow
---
flowchart TD
    A[Raw PDF] -- preprocess --> B[Preprocessed PDF]
    B -- submit to Textract --> C[Textract Json]
    
    subgraph "Parse Json"
        C -- extract cell confidence --> D[OCR Confidence]
        C -- extract Cell geometries --> F[Cell Coordinates]
        C -- extract cell text --> G[OCR Text]
    end

    subgraph "AI-correction"
        D -- check confidence --> E{Confidence > 0.9?}
        E -- yes --> H[Use OCRed Text]
        E -- no --> I[Use LLM to correct text]
        F -.-> I
        G -.-> I
        H -- rule based correction --> K
        I -- rule based correction --> K[Corrected Text]
    end

    K -- modify raw json --> L[Processed Textract Json]
    L -.-> M
    A -.-> M
    subgraph "Frontend"
        M[Manual correction of cell]
        M -- update --> L
        L -- Export Data --> N[csv Files]
    end
```