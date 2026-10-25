use pyo3::prelude::*;

#[pyfunction]
fn answer() -> u8 {
    42
}

#[pymodule(gil_used = false)]
fn maturin3062_probe(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(answer, module)?)?;
    Ok(())
}
