# syntax=docker/dockerfile:1.7

ARG NGINX_IMAGE=nginx:1.29.8-alpine3.23-slim
FROM ${NGINX_IMAGE}

RUN rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /tmp/client_temp /tmp/proxy_temp /tmp/fastcgi_temp /tmp/uwsgi_temp /tmp/scgi_temp \
    && chown -R nginx:nginx /tmp /etc/nginx

COPY nginx.conf /etc/nginx/nginx.conf

USER nginx
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
