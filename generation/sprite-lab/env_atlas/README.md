# Environment Atlas Pipeline

Renderiza 8 assets de ambiente × 8 direções para criar um atlas 8×8 (2048×2048).

## Estrutura

```
env_atlas/
├── asset_selection.json    # Seleção dos 8 assets
├── render_profile.json     # Perfil de renderização
├── run.py                  # Script de execução
└── output/                 # Atlas e renders individuais
```

## Uso

```bash
# Executar pipeline completa
python run.py --blender blender

# Com output personalizado
python run.py --blender blender --output /path/to/output

# Com profile personalizado
python run.py --blender blender --profile /path/to/profile.json
```

## Asset Selection

Cada asset tem:
- `col`: Índice da coluna no atlas (0-7)
- `name`: Nome do asset
- `tile_key`: Chave do TileKey no catálogo
- `capabilities`: Capacidades do tile
- `fbx_path`: Caminho do arquivo FBX

## Render Profile

Perfil `env_atlas_v1`:
- `cell_size`: [256, 256]
- `ortho_scale_mode`: "fit" (ajusta por direção)
- `center_z`: true (centraliza verticalmente)
- `foot_anchor`: [128, 128] (centro da célula)
- `directions`: 8

## Output

- `env_atlas.png`: Atlas 8×8 (2048×2048)
- `render_metadata.json`: Metadados de cada render
- `<asset_name>/`: Renders individuais por asset
