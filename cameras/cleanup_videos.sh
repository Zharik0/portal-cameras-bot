#!/bin/bash

# Скрипт для удаления видеофайлов старше 24 часов
# Путь к директории с видео
CAMERAS_DIR="/home/ftpuser/media/cameras"

# Удаляем .mp4 файлы старше 24 часов
# -maxdepth 1: только в основной директории, без подпапок
# -name "*.mp4": только видеофайлы
# -type f: только файлы
# -mtime +0: файлы, модифицированные более суток назад
# -delete: удалить файлы
find "$CAMERAS_DIR" -maxdepth 1 -name "*.mp4" -type f -mtime +0 -delete

exit 0
