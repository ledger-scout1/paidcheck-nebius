FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV PORT=7860
EXPOSE 7860
CMD ["python3", "web_demo.py"]
