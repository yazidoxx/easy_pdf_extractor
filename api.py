from fastapi import FastAPI, UploadFile, HTTPException, Query
from pathlib import Path
import shutil
import logging
from pdf_processor import PDFLayoutProcessor
from fastapi.responses import FileResponse, JSONResponse
import pdf_processor
import argparse
import os
from dotenv import load_dotenv
import uvicorn

app = FastAPI(
    title="PDF Processor API",
    description="API for processing PDF files and extracting sections",
    version="1.0.0"
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create uploads directory if it doesn't exist
UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

processor = pdf_processor.PDFLayoutProcessor()

@app.post("/process-pdf/")
async def process_pdf(file: UploadFile):
    """
    Process a PDF file to detect and analyze its layout.
    
    Args:
        file (UploadFile): The uploaded PDF file to process. Must be a valid PDF document.
        
    Returns:
        FileResponse: The processed PDF file with layout analysis results.
                     The file will have the same name as the input with '_processed' suffix.
        
    Raises:
        HTTPException (500): If processing fails due to invalid PDF or processing errors.
    """
    try:
        # Create a unique directory for this PDF
        pdf_dir = UPLOADS_DIR / Path(file.filename).stem
        pdf_dir.mkdir(exist_ok=True)
        
        # Save uploaded file
        file_path = pdf_dir / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Process PDF
        output_pdf, output_csv = processor.process_pdf(str(file_path))
        
        return FileResponse(
            output_pdf,
            media_type='application/pdf',
            filename=Path(output_pdf).name
        )
    
    except Exception as e:
        logger.error(f"Error processing PDF: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/extract-text/")
async def extract_text(
    file: UploadFile,
    omit_store_results: bool = Query(
        False,
        description="If true, always re-process PDF and regenerate CSV/TXT even if they exist.",
    ),
):
    """
    Extract text content from a PDF file.
    
    This endpoint processes a PDF file to extract all text content while preserving
    the document's structure and formatting.
    
    Args:
        file (UploadFile): The PDF file to extract text from. Must be a valid PDF document.
        
    Returns:
        JSONResponse: Dictionary containing extracted text in the format:
                     {"text": "extracted text content"}
        
    Raises:
        HTTPException (404): If no text content is found in the PDF.
        HTTPException (500): If text extraction fails due to processing errors.
    """
    try:
        # Create a unique directory for this PDF
        pdf_dir = UPLOADS_DIR / Path(file.filename).stem
        pdf_dir.mkdir(exist_ok=True)
        
        # Save uploaded file
        file_path = pdf_dir / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Create PDFTextExtractor instance
        extractor = pdf_processor.PDFTextExtractor(
            file_path,
            use_store_results=omit_store_results,
        )
        
        # Extract text
        extracted_text = extractor.extract_text()
        
        if not extracted_text:
            return JSONResponse(
                status_code=404,
                content={"message": "No text found in the PDF"}
            )
        
        return JSONResponse(
            content={"text": extracted_text}
        )
    
    except Exception as e:
        logger.error(f"Error extracting text: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/extract-sections/")
async def extract_sections(
    file: UploadFile,
    section_type: str = None,
    omit_store_results: bool = Query(
        False,
        description="If true, always re-process PDF and regenerate CSV/TXT even if they exist.",
    ),
):
    """
    Extract specific sections from a PDF file.
    
    This endpoint processes a PDF file to extract specific sections based on the
    provided section type. It can extract individual sections or all available sections.
    
    Args:
        file (UploadFile): The PDF file to extract sections from. Must be a valid PDF document.
        section_type (str, optional): Type of section to extract. Valid values are:
                                    - "methods": Extracts methods section
                                    - "discussion": Extracts discussion section
                                    - "results": Extracts results section
                                    - "das": Extracts data analysis section
                                    - "all": Extracts all available sections
                                    If None or empty, extracts all sections.
        
    Returns:
        JSONResponse: Dictionary containing extracted section(s):
                     - For specific section: {section_type: "extracted text"}
                     - For all sections: {"sections": {"section_type": "extracted text", ...}}
        
    Raises:
        HTTPException (400): If an invalid section type is provided.
        HTTPException (500): If section extraction fails due to processing errors.
    """
    try:

        # Create a unique directory for this PDF
        pdf_dir = UPLOADS_DIR / Path(file.filename).stem
        pdf_dir.mkdir(exist_ok=True)
        
        # Save uploaded file
        file_path = pdf_dir / file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Create PDFProcessor instance
        extractor = pdf_processor.PDFTextExtractor(
            file_path,
            use_store_results=omit_store_results,
        )
        
        # Validate section type if provided
        valid_sections = ["methods", "discussion", "results", "das", "all"]
        if section_type and section_type.lower() not in valid_sections:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid section type. Must be one of: {', '.join(valid_sections)}"
            )
        
        # Extract sections
        extracted_content = extractor.extract_sections(
            section_type=section_type
        )
        
        # Return appropriate response based on extraction mode
        if section_type == "all" or section_type is None or section_type.strip() == "":
            return JSONResponse(content={"sections": extracted_content})
        else:
            return JSONResponse(content={section_type: extracted_content})
    
    except Exception as e:
        logger.error(f"Error extracting sections: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    
    # Load environment variables from .env file
    load_dotenv()

    parser = argparse.ArgumentParser(description="PDF Processing API server")
    parser.parse_args()

    
    # Get port from environment variables, default to 8000 if not set
    port = int(os.getenv('FAST_API_PORT', 8003))
    backend_workers = int(os.getenv('FAST_API_WORKERS', 4))
    
    uvicorn.run("api:app", host="0.0.0.0", port=port, workers=backend_workers)