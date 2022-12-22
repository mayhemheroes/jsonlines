#! /usr/bin/python3
import atheris
import sys
import logging
import io

logging.disable(logging.CRITICAL)

import jsonlines

def TestOneInput(data):
    fdp = atheris.FuzzedDataProvider(data)

    fp_in = io.BytesIO(data)
    fp_out = io.BytesIO()

    try:
        objs = []
        with jsonlines.Reader(fp_in) as reader:
            repr(reader)
            with jsonlines.Writer(fp_out) as writer:
                writer.write(list(reader.iter()))
    except jsonlines.InvalidLineError:
        pass  # Handled exception


def main():
    atheris.instrument_all()
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
