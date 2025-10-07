FROM ubuntu:22.04

# BlockSci requires manual compilation - this is a placeholder
# See: https://github.com/citp/BlockSci for installation instructions

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Placeholder - BlockSci must be compiled from source
CMD ["tail", "-f", "/dev/null"]
