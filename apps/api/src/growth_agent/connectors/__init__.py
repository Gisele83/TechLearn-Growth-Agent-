"""Connecteurs externes interchangeables.

Chaque famille (CRM, LLM, recherche, messagerie, produits) expose une interface
abstraite et au moins une implémentation `mock` utilisable hors ligne et sans
frais. Les implémentations réelles sont chargées uniquement si la configuration
les active explicitement.
"""
