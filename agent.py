from workflow import app
import logging

question = "Как часто надо чистить стиральную машину?"
inputs = {"question": question}

for output in app.stream(inputs):
    for key, value in output.items():
        logging.info(f"Node '{key}':")

print(value["generation"])