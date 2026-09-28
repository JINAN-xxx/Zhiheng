FROM nginx:latest
WORKDIR /usr/share/nginx/html

# dist 是随源码仓库发布的预构建前端产物。
COPY dist .

# 暴露端口
EXPOSE 80

# 启动nginx
CMD ["nginx", "-g", "daemon off;"]
