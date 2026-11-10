# Frontend image: static HTML/CSS/JS served by nginx. It knows the backend only through nginx.conf / config.js.
FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY *.html *.css *.js /usr/share/nginx/html/
