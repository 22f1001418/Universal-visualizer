"""Business-logic services used by HTTP routers.

Layering contract (enforced by import-linter in Task 13):
  services/ never imports from api/. services/ depends only on {llm,
  viz_generator, store, models, config, github_publisher, orchestrator,
  agents}.
"""
