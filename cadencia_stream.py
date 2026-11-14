"""Divide trechos grandes de texto para exibição gradual."""
import time


def partes(texto):
    tamanho = max(12, (len(texto) + 19) // 20)
    for inicio in range(0, len(texto), tamanho):
        if inicio:
            time.sleep(0.015)
        yield texto[inicio:inicio + tamanho]
