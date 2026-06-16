# Hugging Face Spaces (Docker SDK) image for the v2 Streamlit app.
# HF serves the container on port 7860 and runs it as a non-root user (uid 1000).
FROM python:3.12-slim

# Run as the uid HF Spaces expects; give it a writable home.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /home/user/app

# Install dependencies first so this layer caches across code changes.
COPY --chown=user:user requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

# Copy the application (data/processed parquet, labor data, dictionary, src, config).
COPY --chown=user:user . ./

# The app writes the generated workbook here before serving it for download.
RUN mkdir -p output/reports

EXPOSE 7860

# Bind to 0.0.0.0:7860 and disable CORS/XSRF so the app works inside the HF iframe.
CMD ["streamlit", "run", "src/app_v2.py", \
     "--server.port=7860", "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", "--server.enableXsrfProtection=false", \
     "--browser.gatherUsageStats=false"]
