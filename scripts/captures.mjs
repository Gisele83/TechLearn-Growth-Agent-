/**
 * Captures de la page « Performance commerciale ».
 *
 * À lancer depuis `apps/web` (c'est là que Playwright est installé), avec
 * l'API et l'interface démarrées sur leurs ports habituels :
 *
 *     scripts/demo-captures.sh
 *
 * Les captures servent de preuve reproductible : même jeu de données, même
 * rendu. Aucun envoi n'est effectué — la base de démonstration est écrite
 * directement, et le mode d'envoi réel reste désactivé.
 */

import { chromium } from 'playwright'

const OUT = '/home/user/techlearn-growth-agent/docs/captures'
const BASE = 'http://localhost:5173'

// Chromium récent : le mode « headless » historique a été retiré du binaire,
// et Playwright le demande encore par défaut. On passe donc le nouveau mode
// à la main plutôt que de laisser le lancement échouer.
const navigateur = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH || undefined,
  headless: false,
  args: ['--headless=new', '--no-sandbox', '--hide-scrollbars'],
})

async function session(role, mdp, theme) {
  const contexte = await navigateur.newContext({
    viewport: { width: 1440, height: 1200 },
    deviceScaleFactor: 2,
    colorScheme: theme,
    locale: 'fr-FR',
  })
  const page = await contexte.newPage()
  await page.goto(BASE, { waitUntil: 'networkidle' })
  await page.getByLabel(/Adresse|Email|Identifiant/i).fill(role)
  await page.getByLabel(/Mot de passe/i).fill(mdp)
  await page.getByRole('button', { name: /Se connecter/i }).click()
  await page.waitForSelector('nav')
  return { contexte, page }
}

async function capturer(page, nom) {
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${OUT}/${nom}.png`, fullPage: true })
  console.log('→', nom)
}

// 1. Vue complète, compte commercial.
{
  const { contexte, page } = await session(
    'commercial@growth.example.org', 'DemoCommercial!2026', 'light',
  )
  await page.getByRole('link', { name: 'Performance commerciale' }).click()
  await page.waitForSelector('text=Volumes par cohorte')
  await capturer(page, 'performance-01-vue-complete')

  // 2. Filtre sur une campagne.
  await page.getByLabel('Période').selectOption('12')
  await page.waitForTimeout(500)
  await capturer(page, 'performance-02-periode-12-mois')

  // 3. Filtre sur une seule cohorte.
  const options = await page.getByLabel('Cohorte').locator('option').all()
  const valeurs = await Promise.all(options.map((o) => o.getAttribute('value')))
  const premiere = valeurs.find((v) => v)
  if (premiere) {
    await page.getByLabel('Cohorte').selectOption(premiere)
    await page.waitForTimeout(400)
    await capturer(page, 'performance-03-une-cohorte')
    await page.getByLabel('Cohorte').selectOption('')
  }

  // 4. Thème sombre.
  await contexte.close()
}
{
  const { contexte, page } = await session(
    'commercial@growth.example.org', 'DemoCommercial!2026', 'dark',
  )
  await page.getByRole('link', { name: 'Performance commerciale' }).click()
  await page.waitForSelector('text=Volumes par cohorte')
  await capturer(page, 'performance-04-theme-sombre')
  await contexte.close()
}

// 5. Produit sans aucune cohorte : l'état vide.
{
  const { contexte, page } = await session(
    'lecteur@growth.example.org', 'DemoLecteur!2026', 'light',
  )
  await page.getByRole('link', { name: 'Performance commerciale' }).click()
  await page.waitForSelector('text=Volumes par cohorte')
  await page.getByLabel('Produit').selectOption('maintrix')
  await page.waitForSelector('text=Aucune cohorte sur la période')
  await capturer(page, 'performance-05-etat-vide-lecteur')

  // 6. API en échec.
  await page.route('**/api/v1/cohorts*', (route) => route.abort('failed'))
  await page.getByLabel('Produit').selectOption('educore')
  await page.waitForSelector('text=Indicateurs indisponibles')
  await capturer(page, 'performance-06-erreur-api')
  await contexte.close()
}

await navigateur.close()
console.log('Captures terminées.')
