# Two stages: build the Angular app with Node, then serve the static output
# with nginx. The build stage's node_modules never reach the final image.

FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# The @angular-devkit/build-angular:application builder (angular.json) nests
# browser output under dist/<project>/browser — outputPath here is dist/lead.
FROM nginx:alpine AS runtime
COPY --from=build /app/dist/lead/browser /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://127.0.0.1:80/ || exit 1
