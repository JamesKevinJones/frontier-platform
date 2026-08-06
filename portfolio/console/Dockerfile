# Two stages: build the bundle with Node, serve it with nginx. The runtime image
# carries no toolchain, which keeps it small and removes npm from the attack surface.

FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
