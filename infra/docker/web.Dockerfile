# Image de développement local pour l'interface (serveur Vite).
# La production sert des fichiers statiques derrière un reverse proxy — voir
# docs/09-dossier-deploiement.md.

FROM node:22-alpine

WORKDIR /app

COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install

COPY apps/web ./

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
