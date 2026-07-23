FROM mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04
WORKDIR /app
COPY cpp ./cpp
RUN cmake -S cpp -B build/cpp && cmake --build build/cpp
CMD ["bash", "-lc", "echo 'cpp coordinator scaffold container ready' && sleep infinity"]
