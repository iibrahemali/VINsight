FROM node:22-slim
RUN corepack enable

WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .

# Rewrites are resolved at build time, so the backend address must be known here.
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=$BACKEND_URL
RUN pnpm build

EXPOSE 3000
CMD ["pnpm", "start"]
