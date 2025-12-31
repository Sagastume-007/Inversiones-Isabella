"""
Módulo para consultar información de productos desde APIs públicas
usando códigos de barras (EAN/UPC)
"""
import requests
from typing import Optional, Dict

class ProductLookup:
    """Consulta múltiples APIs públicas para obtener información de productos"""
    
    def __init__(self):
        self.apis = [
            self._openfoodfacts,
            self._upcitemdb,
            self._barcodelookup
        ]
    
    def buscar_producto(self, codigo_barras: str) -> Optional[Dict]:
        """
        Busca un producto por código de barras en múltiples APIs
        Retorna el primer resultado exitoso o None
        """
        codigo_barras = str(codigo_barras).strip()
        
        for api_func in self.apis:
            try:
                resultado = api_func(codigo_barras)
                if resultado:
                    return resultado
            except Exception as e:
                print(f"Error en {api_func.__name__}: {e}")
                continue
        
        return None
    
    def _openfoodfacts(self, codigo: str) -> Optional[Dict]:
        """Open Food Facts - Base de datos colaborativa de alimentos"""
        url = f"https://world.openfoodfacts.org/api/v0/product/{codigo}.json"
        
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            return None
        
        data = response.json()
        if data.get("status") != 1:
            return None
        
        product = data.get("product", {})
        
        return {
            "nombre": product.get("product_name", ""),
            "marca": product.get("brands", ""),
            "categorias": product.get("categories", ""),
            "imagen_url": product.get("image_url", ""),
            "cantidad": product.get("quantity", ""),
            "fuente": "Open Food Facts"
        }
    
    def _upcitemdb(self, codigo: str) -> Optional[Dict]:
        """UPC Item DB - Requiere API key gratuita en upcitemdb.com"""
        # Para usar esta API necesitas registrarte en https://www.upcitemdb.com/
        # y obtener una API key gratuita
        
        API_KEY = "TU_API_KEY_AQUI"  # Reemplazar con tu key
        
        if API_KEY == "TU_API_KEY_AQUI":
            return None  # Skip si no hay key configurada
        
        url = f"https://api.upcitemdb.com/prod/trial/lookup"
        headers = {
            "Accept": "application/json",
            "user_key": API_KEY
        }
        params = {"upc": codigo}
        
        response = requests.get(url, headers=headers, params=params, timeout=5)
        if response.status_code != 200:
            return None
        
        data = response.json()
        if not data.get("items"):
            return None
        
        item = data["items"][0]
        
        return {
            "nombre": item.get("title", ""),
            "marca": item.get("brand", ""),
            "categorias": ", ".join(item.get("category", [])),
            "imagen_url": item.get("images", [""])[0] if item.get("images") else "",
            "descripcion": item.get("description", ""),
            "fuente": "UPC Item DB"
        }
    
    def _barcodelookup(self, codigo: str) -> Optional[Dict]:
        """Barcode Lookup API - Alternativa gratuita con límites"""
        # Registrarse en https://www.barcodelookup.com/api para obtener key
        
        API_KEY = "TU_API_KEY_AQUI"  # Reemplazar con tu key
        
        if API_KEY == "TU_API_KEY_AQUI":
            return None
        
        url = f"https://api.barcodelookup.com/v3/products"
        params = {
            "barcode": codigo,
            "key": API_KEY
        }
        
        response = requests.get(url, params=params, timeout=5)
        if response.status_code != 200:
            return None
        
        data = response.json()
        if not data.get("products"):
            return None
        
        product = data["products"][0]
        
        return {
            "nombre": product.get("title", ""),
            "marca": product.get("brand", ""),
            "categorias": product.get("category", ""),
            "imagen_url": product.get("images", [""])[0] if product.get("images") else "",
            "descripcion": product.get("description", ""),
            "fuente": "Barcode Lookup"
        }


# Ejemplo de uso
if __name__ == "__main__":
    lookup = ProductLookup()
    
    # Ejemplo con código de Coca-Cola (varía por país)
    codigo_prueba = "5449000000996"
    
    print(f"Buscando producto: {codigo_prueba}")
    resultado = lookup.buscar_producto(codigo_prueba)
    
    if resultado:
        print("\n✓ Producto encontrado:")
        for key, value in resultado.items():
            print(f"  {key}: {value}")
    else:
        print("\n✗ Producto no encontrado en ninguna base de datos")