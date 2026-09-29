# Compresso - C Test Suite Type Sanitiser

module TypeSanitiser
  def self.sanitise_c_identifier(unsanitised)
    # convert filename to valid C identifier by replacing invalid chars with '_'
    unsanitised.gsub(/[-\/\\.,\s]/, '_')
  end
end
